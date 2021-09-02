import flask
from flask.templating import render_template_string
import werkzeug

import json
import os

from tsuu import models

app = flask.current_app
bp = flask.Blueprint('download', __name__)

@bp.route('/items/<string:item_id>/', defaults={'path': None})
@bp.route('/items/<string:item_id>/<path:path>')
def download(item_id, path):
    """
    Serves up the items.

    Shows a simple directory listing (scrapable by curl) based on the known items.

    Note that you shouldn't do this in production. 
    In production, serve /items/ to the items directory 
    instead with directory listing enabled, thereby overriding
    this endpoint.

    There is a special error page coded if you try to visit this path while the server is disabled.
    """
    # Block out the server.
    if not app.config["FLASK_SERVE_ITEMS"]:
        return flask.render_template("download_disabled.html")

    item = models.Item.by_id(item_id)

    # THIS IS STUPID - TAKES CARE OF SEPARATORS
    base_dir = f"{os.getcwd()}{os.sep}{app.config['ROOT_FOLDER']}{os.sep}{app.config['ITEM_FOLDER']}{os.sep}{item.item_directory}"
    if not path:
        path = base_dir
    else:
        # VALIDATION OF PATH SECURITY TO STOP ESCAPES THIS IS VERY IMPORTANT
        path = werkzeug.security.safe_join(base_dir, path)

    # ABORT INVALID PATHS
    if not path:
        flask.abort(404)

    # We know the path is safe. Now lets check if it exists.
    if not os.path.exists(path):
        flask.abort(404)

    # Ok so it exists, next: is it a directory?
    if os.path.isdir(path):
        return flask.render_template("download.html", files=os.listdir(path))

    # It's not, let's have flask deliver the rest.
    print("Flask delivers: {}/{}".format(os.path.dirname(path), os.path.basename(path)))
    return flask.send_from_directory(os.path.dirname(path), os.path.basename(path))
