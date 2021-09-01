from wtforms.validators import ValidationError
from torrent import TorrentHandler

def get_handler_by_mimetype(mimetype):
    if mimetype == "application/x-bittorrent":
        return TorrentHandler()
    else:
        raise ValidationError(f"No handlers exist for this mimetype. {mimetype}")
