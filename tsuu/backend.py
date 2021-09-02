import hashlib
from io import BytesIO
import json
import os
import re
from datetime import datetime, timedelta
from ipaddress import ip_address

import flask
from werkzeug import secure_filename

import sqlalchemy
from orderedset import OrderedSet

from tsuu import models, utils
from tsuu.extensions import db

app = flask.current_app

# Blacklists for _validate_item_filenames
# TODO: consider moving to config.py?
CHARACTER_BLACKLIST = [
    '\u202E',  # RIGHT-TO-LEFT OVERRIDE
]
FILENAME_BLACKLIST = [
    # Windows reserved filenames
    'con',
    'nul',
    'prn',
    'aux',
    'com0', 'com1', 'com2', 'com3', 'com4', 'com5', 'com6', 'com7', 'com8', 'com9',
    'lpt0', 'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7', 'lpt8', 'lpt9',
]

# Invalid RSS characters regex, used to sanitize some strings
ILLEGAL_XML_CHARS_RE = re.compile(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]')


def sanitize_string(string, replacement='\uFFFD'):
    ''' Simply replaces characters based on a regex '''
    return ILLEGAL_XML_CHARS_RE.sub(replacement, string)

def calculate_short_hash(data):
    h = hashlib.sha256()
    d = data.read()
    h.update(d)
    return h.hexdigest()[:6]

class ItemExtraValidationException(Exception):
    def __init__(self, errors={}):
        self.errors = errors


@utils.cached_function
def get_category_id_map():
    ''' Reads database for categories and turns them into a dict with
        ids as keys and name list as the value, ala
        {'1_0': ['Anime'], '1_2': ['Anime', 'English-translated'], ...} '''
    cat_id_map = {}
    for main_cat in models.MainCategory.query:
        cat_id_map[main_cat.id_as_string] = [main_cat.name]
        for sub_cat in main_cat.sub_categories:
            cat_id_map[sub_cat.id_as_string] = [main_cat.name, sub_cat.name]
    return cat_id_map


def _replace_utf8_values(dict_or_list):
    ''' Will replace 'property' with 'property.utf-8' and remove latter if it exists.
        Thanks, bitcomet! :/ '''
    did_change = False
    if isinstance(dict_or_list, dict):
        for key in [key for key in dict_or_list.keys() if key.endswith('.utf-8')]:
            dict_or_list[key.replace('.utf-8', '')] = dict_or_list.pop(key)
            did_change = True
        for value in dict_or_list.values():
            did_change = _replace_utf8_values(value) or did_change
    elif isinstance(dict_or_list, list):
        for item in dict_or_list:
            did_change = _replace_utf8_values(item) or did_change
    return did_change


def _recursive_dict_iterator(source):
    ''' Iterates over a given dict, yielding (key, value) pairs,
        recursing inside any dicts. '''
    # TODO Make a proper dict-filetree walker
    for key, value in source.items():
        yield (key, value)

        if isinstance(value, dict):
            for kv in _recursive_dict_iterator(value):
                yield kv


def _validate_item_filenames(item):
    ''' Checks path parts of a item's filetree against blacklisted characters
        and filenames, returning False on rejection '''
    file_tree = json.loads(item.filelist.filelist_blob.decode('utf-8'))

    for path_part, value in _recursive_dict_iterator(file_tree):
        if path_part.rsplit('.', 1)[0].lower() in FILENAME_BLACKLIST:
            return False
        if any(True for c in CHARACTER_BLACKLIST if c in path_part):
            return False

    return True


def validate_item_post_upload(item, upload_form=None):
    ''' Validates a Item instance before it's saved to the database.
        Enforcing user-and-such-based validations is more flexible here vs WTForm context '''
    errors = {
        'item_file': []
    }

    # Encorce minimum size for userless uploads
    minimum_anonymous_item_size = app.config['MINIMUM_ANONYMOUS_ITEM_SIZE']
    if item.user is None and item.filesize < minimum_anonymous_item_size:
        errors['item_file'].append('Item too small for an anonymous uploader')

    if not _validate_item_filenames(item):
        errors['item_file'].append('Item has forbidden characters in filenames')

    # Remove keys with empty lists
    errors = {k: v for k, v in errors.items() if v}
    if errors:
        if upload_form:
            # Add error messages to the form fields
            for field_name, field_errors in errors.items():
                getattr(upload_form, field_name).errors.extend(field_errors)
            # Clear out the wtforms dict to force a regeneration
            upload_form._errors = None

        raise ItemExtraValidationException(errors)


def check_uploader_ratelimit(user):
    ''' Figures out if user (or IP address from flask.request) may
        upload within upload ratelimit.
        Returns a tuple of current datetime, count of items uploaded
        within burst duration and timestamp for next allowed upload. '''
    now = datetime.utcnow()
    next_allowed_time = now

    Item = models.Item

    def filter_uploader(query):
        if user:
            return query.filter(sqlalchemy.or_(
                Item.user == user,
                Item.uploader_ip == ip_address(flask.request.remote_addr).packed))
        else:
            return query.filter(Item.uploader_ip == ip_address(flask.request.remote_addr).packed)

    time_range_start = datetime.utcnow() - timedelta(seconds=app.config['UPLOAD_BURST_DURATION'])
    # Count items uploaded by user/ip within given time period
    item_count_query = db.session.query(sqlalchemy.func.count(Item.id))
    item_count = filter_uploader(item_count_query).filter(
        Item.created_time >= time_range_start).scalar()

    # If user has reached burst limit...
    if item_count >= app.config['MAX_UPLOAD_BURST']:
        # Check how long ago their latest item was (we know at least one will exist)
        last_item = filter_uploader(Item.query).order_by(Item.created_time.desc()).first()
        after_timeout = last_item.created_time + timedelta(seconds=app.config['UPLOAD_TIMEOUT'])

        if now < after_timeout:
            next_allowed_time = after_timeout

    return now, item_count, next_allowed_time


def handle_item_upload(upload_form, uploading_user=None, fromAPI=False):
    ''' Stores an item to the database.
        May throw ItemExtraValidationException if the form/item fails
        post-WTForm validation! Exception messages will also be added to their
        relevant fields on the given form. '''
    print("Handling upload")
    item_data = BytesIO()
    upload_form.submission_file.data.save(item_data)

    # Anonymous uploaders and non-trusted uploaders
    no_or_new_account = (not uploading_user
                         or (uploading_user.age < app.config['RATELIMIT_ACCOUNT_AGE']
                             and not uploading_user.is_trusted))

    if app.config['RATELIMIT_UPLOADS'] and no_or_new_account:
        now, _, next_time = check_uploader_ratelimit(uploading_user)
        if next_time > now:
            # This will flag the dialog in upload.html red and tell API users what's wrong
            upload_form.ratelimit.errors = ["You've gone over the upload ratelimit."]
            raise ItemExtraValidationException()

    if not uploading_user:
        if app.config['RAID_MODE_LIMIT_UPLOADS']:
            # XXX TODO: rename rangebanned to something more generic
            upload_form.rangebanned.errors = [app.config['RAID_MODE_UPLOADS_MESSAGE']]
            raise ItemExtraValidationException()
        elif models.RangeBan.is_rangebanned(ip_address(flask.request.remote_addr).packed):
            upload_form.rangebanned.errors = ["Your IP is banned from "
                                              "uploading anonymously."]
            raise ItemExtraValidationException()

    # Get data from upload fields
    display_name = upload_form.display_name.data.strip()
    information = (upload_form.information.data or '').strip()
    description = (upload_form.description.data or '').strip()

    # Sanitize fields
    display_name = sanitize_string(display_name)
    information = sanitize_string(information)
    description = sanitize_string(description)

    item_data.seek(0, os.SEEK_END)
    item_filesize = item_data.tell()
    item_data.seek(0)

    item_id = calculate_short_hash(item_data)
    item_data.seek(0)

    item = models.Item(display_name=display_name,
                            item_directory=item_id,
                            information=information,
                            description=description,
                            filesize=item_filesize,
                            user=uploading_user,
                            uploader_ip=ip_address(flask.request.remote_addr).packed)
    print("Made model")

    # Store file
    item_directory = f"{app.config['ROOT_FOLDER']}/{app.config['ITEM_FOLDER']}/{item.item_directory}"
    filename = upload_form.submission_file.data.filename
    os.makedirs(item_directory, exist_ok=True)

    with open(os.path.join(item_directory, filename), 'wb') as out_file:
        out_file.write(item_data.getbuffer())
    print("stored file")

    item.stats = models.Statistic()

    # Fields with default value will be None before first commit, so set .flags
    item.flags = 0

    item.anonymous = upload_form.is_anonymous.data if uploading_user else True
    item.hidden = upload_form.is_hidden.data
    item.remake = upload_form.is_remake.data
    item.complete = upload_form.is_complete.data
    # Copy trusted status from user if possible
    can_mark_trusted = uploading_user and uploading_user.is_trusted
    # To do, automatically mark trusted if user is trusted unless user specifies otherwise
    item.trusted = upload_form.is_trusted.data if can_mark_trusted else False

    # Only allow mods to upload locked items
    can_mark_locked = uploading_user and uploading_user.is_moderator
    item.comment_locked = upload_form.is_comment_locked.data if can_mark_locked else False

    # Set category ids
    item.main_category_id, item.sub_category_id = \
        upload_form.category.parsed_data.get_category_ids()

    print("making tree")
    # Recurse over the item directory to create the file list
    def get_file_tree(root):
        def file_tree(path, d):
            name = os.path.basename(path)

            if os.path.isdir(path):
                d[name] = {}
                for x in os.listdir(path):
                    file_tree(os.path.join(path,x), d[name])
            else:
                # Path is a file; save it
                d[name] = os.path.getsize(path)
            return d

        return file_tree(root, dict())

    parsed_file_tree = get_file_tree(item_directory)
    print("made tree")

    json_bytes = json.dumps(parsed_file_tree, separators=(',', ':')).encode('utf8')
    item.filelist = models.Filelist(filelist_blob=json_bytes)

    db.session.add(item)
    db.session.flush()
    db.session.commit()

    return item
