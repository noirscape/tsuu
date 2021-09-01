# This is an example file handler. Use this as a reference to implement your own but do NOT sublcass it.

class SampleHandler:
    # Boilerplate
    def __init__(self) -> None:
        pass

    # Implement here your upload validation.
    # Check if the upload is actually the filetype it claims to be in here.
    def validate_upload(self, form, field):
        pass