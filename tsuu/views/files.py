import hashlib
import json
import os
import shutil

import flask
from sqlalchemy.sql import base
import werkzeug

from tsuu import models, backend

app = flask.current_app
bp = flask.Blueprint('files', __name__)


@bp.route("/view/<int:item_id>/edit/files")
def edit(item_id):
    item = models.Item.by_id(item_id)

    editor = flask.g.user

    if not item:
        flask.abort(404)

    # Only allow admins edit deleted items
    if item.deleted and not (editor and editor.is_moderator):
        flask.abort(404)

    # Only allow item owners or admins edit items
    if not editor or not (editor is item.user or editor.is_moderator):
        flask.abort(403)

    return flask.render_template("files.html", item=item)

# API FILE EDITING FUNCTIONS AHEAD!
def _md5_string(string):
    """MD5s a string"""
    md5 = hashlib.md5()
    md5.update(bytes(string, 'utf-8'))
    return md5.hexdigest()

def _hash_file(path, filename):
    """Returns an md5 hash based on the filename and the mtime."""
    mtime = os.path.getmtime(path)
    return _md5_string(f"{filename}-{mtime}")

def _get_path(base_dir, path_string):
    path = json.loads(path_string)
    filtered_path = list(filter(None, path))
    full_path = "/".join(filtered_path)
    return werkzeug.security.safe_join(base_dir, full_path)

@bp.route('/view/<int:item_id>/edit/files/manager', methods=['POST'])
def get_files_list(item_id):
    item = models.Item.by_id(item_id)

    editor = flask.g.user

    if not item:
        flask.abort(404)

    # Only allow admins edit deleted items
    if item.deleted and not (editor and editor.is_moderator):
        flask.abort(404)

    # Only allow item owners or admins edit items
    if not editor or not (editor is item.user or editor.is_moderator):
        flask.abort(403)


    action = flask.request.form.get('action')
    base_dir = f"{app.config['ROOT_FOLDER']}/{app.config['ITEM_FOLDER']}/{item.item_directory}"

    if action == "refresh":
        # This ugly line gets the "proper" path from the function.
        # To save yourself a headache: it converts the path from the form to JSON, 
        # then filters that path to remove empty strings -manager starts with an empty path as default-, before joining it
        # with a slash.
        path_string = flask.request.form.get("path")
        full_path = _get_path(base_dir, path_string)

        entries = []
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            entry = {}
            entry["id"] = item
            entry["name"] = item
            if os.path.isdir(item_path):
                entry["type"] = "folder"
                entry["hash"] = _md5_string(item)
            else:
                entry["type"] = "file"
                entry["size"] = os.path.getsize(item_path)
                entry["hash"] = _hash_file(item_path, item)
            entries.append(entry)

        response = {
            "success": True,
            "entries": entries
        }

        return json.dumps(response)
    elif action == "rename":
        # Get our data from the form
        original = flask.request.form.get("id")
        path_string = flask.request.form.get("path")
        new_name = flask.request.form.get("newname")

        # Get the new paths for os.rename
        full_path = _get_path(base_dir, path_string)
        original_path = werkzeug.security.safe_join(full_path, original)
        new_path = werkzeug.security.safe_join(full_path, new_name)

        # Do the rename
        os.rename(original_path, new_path)

        # Compile a response value.
        entry = {
            "id": new_name,
            "name": new_name,
        }
        if os.path.isdir(new_path):
            entry["type"] = "folder"
            entry["hash"] = _md5_string(os.path.basename(new_path))
        else:
            entry["type"] = "file"
            entry["size"] = os.path.getsize(new_path)
            entry["hash"] = _hash_file(new_path, new_name)

        response = {
            "success": True,
            "entry": entry
        }

        backend.handle_item_change(item.id)

        return json.dumps(response)
    elif action == "newfolder":
        # Get our data from the form
        path_string = flask.request.form.get("path")
        full_path = _get_path(base_dir, path_string)

        # assuming no new folder exists yet
        folder_name = flask.request.form.get("name", "new_folder").strip("/")
        new_path = werkzeug.security.safe_join(full_path, folder_name)

        if os.path.exists(new_path):
            if flask.request.form.get("name") != None:
                return {"success": False, "error": "Folder already exists!"}
            # OOPS IT EXISTS
            folder_not_found = True
            attempts = 0
            while folder_not_found:
                folder_name = f"new_folder_{attempts}"
                new_path = os.path.join(full_path, folder_name)
                if not os.path.exists(new_path):
                    folder_not_found = False
                else:
                    attempts += 1

        # make the path
        os.mkdir(new_path)

        # Compile a response value
        entry = {
            "id": folder_name,
            "name": folder_name,
            "type": "folder",
            "hash": _md5_string(folder_name)
        }

        response = {
            "success": True,
            "entry": entry
        }

        backend.handle_item_change(item.id)

        return json.dumps(response)
    elif action == "delete":
        # Get our data from the form
        path_string = flask.request.form.get("path")
        full_path = _get_path(base_dir, path_string)
        paths_to_delete = json.loads(flask.request.form.get("ids"))
        full_paths_to_delete = []

        for path in paths_to_delete:
            full_paths_to_delete.append(werkzeug.security.safe_join(full_path, path))

        if not all(full_paths_to_delete):
            flask.abort(422)

        for path in full_paths_to_delete:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

        response = {
            "success": True,
        }

        backend.handle_item_change(item.id)

        return json.dumps(response)
    elif action == "upload":
        # get all relevant data
        path_string = flask.request.form.get('path')
        full_path = _get_path(base_dir, path_string)

        filename = flask.request.form.get('name')
        size = flask.request.form.get('size')
        file_data = flask.request.files.get("file")

        # since this is writing, triple check
        if not all([path_string, full_path, filename, size, file_data]):
            flask.abort(400)

        # get our destination path
        write_path = werkzeug.security.safe_join(full_path, filename.strip("/"))

        # block exploits...
        if not write_path:
            flask.abort(422)

        # write the file
        os.makedirs(os.path.dirname(write_path), exist_ok=True)
        file_data.save(write_path)

        # check the filesize
        actual_size = str(os.path.getsize(write_path))
        if actual_size != size:
            os.remove(write_path)
            return {"success": False, "error": f"Wrong filesize submitted, received size {actual_size} but param says {size}."}

        # build response
        entry = {
            "id": filename,
            "name": filename,
            "type": "folder",
            "hash": _hash_file(write_path, filename)
        }

        response = {
            "success": True,
            "entry": entry
        }

        backend.handle_item_change(item.id)

        return json.dumps(response)
    elif action == "copy":
        srcpath = flask.request.form.get("srcpath")
        srcids = json.loads(flask.request.form.get("srcids")) # source filenames
        destpath = flask.request.form.get("destpath")
        prepare = flask.request.form.get("prepare") # should we just return if it would overwrite?

        full_srcpath = _get_path(base_dir, srcpath) # source folder
        full_destpath = _get_path(base_dir, destpath) # destination folder

        if not all([full_srcpath, full_destpath]):
            flask.abort(422)

        srcfiles = []
        destfiles = []
        overwrite = 0
        for sourcefile in srcids:
            src = werkzeug.security.safe_join(full_srcpath, sourcefile)
            dest = werkzeug.security.safe_join(full_destpath, sourcefile)
            if not all([src, dest]):
                flask.abort(422) # quit on malicious nonsense
            if dest == src: # are these the same?
                safe_file_name_not_found = True
                attempts = 0
                while safe_file_name_not_found:
                    basename, ext = os.path.splitext(sourcefile)
                    filename = f"{basename}_{attempts}{ext}"
                    dest = werkzeug.security.safe_join(full_destpath, filename)
                    if not sourcefile:
                        flask.abort(422) # quit on malicious nonsense
                    if not os.path.exists(dest):
                        safe_file_name_not_found = False
                    else:
                        attempts += 1

            if os.path.exists(dest): # does it exist
                overwrite += 1
                if any([os.path.isfile(src) and os.path.isdir(dest), os.path.isdir(src) and os.path.isfile(dest)]):
                    # We can't copy a file into a folder which has a directory of the same name!
                    response = {
                        "success": False,
                        "error": "Attempted copy of a file/folder into a folder containing a folder/file of the same name."
                    }
                    return json.dumps(response)

            destfiles.append(dest)
            srcfiles.append(src)

        if prepare:
            response = {
                "success": True,
                "overwrite": overwrite,
                "dest_files": destfiles,
                "src_files": srcfiles
            }
            return json.dumps(response)
        else:
            if len(destfiles) != 1: 
                # our frontend is coded to send the copy jobs 1 at a time.
                # we don't want multiple copy jobs.
                flask.abort(422)

            if os.path.isdir(srcfiles[0]):
                shutil.copytree(srcfiles[0], destfiles[0], dirs_exist_ok=True)
            else:
                shutil.copyfile(srcfiles[0], destfiles[0])

            response = {
                "success": True,
            }

            backend.handle_item_change(item.id)

            return json.dumps(response)
    elif action == "move":
        # get form data
        srcpath = flask.request.form.get("srcpath")
        srcids = json.loads(flask.request.form.get("srcids"))
        destpath = flask.request.form.get("destpath")
        prepare = flask.request.form.get("prepare")

        full_srcpath = _get_path(base_dir, srcpath)
        full_destpath = _get_path(base_dir, destpath)

        # bogus data checks
        if not all([full_srcpath, full_destpath]):
            flask.abort(422)

        operations = []
        overwrite = 0
        for sourcefile in srcids:
            src = werkzeug.security.safe_join(full_srcpath, sourcefile)
            dest = werkzeug.security.safe_join(full_destpath, sourcefile)

            if not all([src, dest]):
                flask.abort(422)

            if os.path.exists(dest):
                overwrite += 1
                if any([os.path.isfile(src) and os.path.isdir(dest), os.path.isdir(src) and os.path.isfile(dest)]):
                    # We can't move a file into a folder which has a directory of the same name!
                    response = {
                        "success": False,
                        "error": "Attempted move of a file/folder into a folder containing a folder/file of the same name."
                    }
                    return json.dumps(response)
            operations.append({
                "src": src,
                "dest": dest,
            })

        if prepare:
            response = {
                "success": True,
                "overwrite": overwrite
            }

            return json.dumps(response)

        for operation in operations:
            shutil.move(operation["src"], operation["dest"])

        backend.handle_item_change(item.id)

        response = {
            "success": True,
            "error": "Not implemented."
        }

        return json.dumps(response)
    else:
        response = {
            "success": False,
            "error": "Invalid action."
        }


@bp.route('/view/<int:item_id>/edit/refresh_index')
def refresh(item_id):
    item = models.Item.by_id(item_id)

    editor = flask.g.user

    if not item:
        flask.abort(404)

    # Only allow admins edit deleted items
    if item.deleted and not (editor and editor.is_moderator):
        flask.abort(404)

    # Only allow item owners or admins edit items
    if not editor or not (editor is item.user or editor.is_moderator):
        flask.abort(403)

    backend.handle_item_change(item.id)

    flask.flash("Successfully refreshed the file index!")

    return flask.redirect(flask.url_for('items.edit', item_id=item_id))
