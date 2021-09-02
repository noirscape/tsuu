import functools
import os
import re

import flask
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from flask_wtf.recaptcha import RecaptchaField
from flask_wtf.recaptcha.validators import Recaptcha as RecaptchaValidator
from wtforms import (BooleanField, HiddenField, PasswordField, SelectField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, EqualTo, Length, Optional, Regexp,
                                StopValidation, ValidationError)
from wtforms.widgets import HTMLString  # For DisabledSelectField
from wtforms.widgets import Select as SelectWidget  # For DisabledSelectField
from wtforms.widgets import html_params

import dns.exception
import dns.resolver

from tsuu import models, utils
from tsuu.extensions import config
from tsuu.models import User

app = flask.current_app

class Unique(object):

    """ validator that checks field uniqueness """

    def __init__(self, model, field, message=None):
        self.model = model
        self.field = field
        if not message:
            message = 'This element already exists'
        self.message = message

    def __call__(self, form, field):
        check = self.model.query.filter(self.field == field.data).first()
        if check:
            raise ValidationError(self.message)


def stop_on_validation_error(f):
    ''' A decorator which will turn raised ValidationErrors into StopValidations '''
    @functools.wraps(f)
    def decorator(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValidationError as e:
            # Replace the error with a StopValidation to stop the validation chain
            raise StopValidation(*e.args) from e
    return decorator


def recaptcha_validator_shim(form, field):
    if app.config['USE_RECAPTCHA']:
        return RecaptchaValidator()(form, field)
    else:
        # Always pass validating the recaptcha field if disabled
        return True


def upload_recaptcha_validator_shim(form, field):
    ''' Selectively does a recaptcha validation '''
    if app.config['USE_RECAPTCHA']:
        # Recaptcha anonymous and new users
        if not flask.g.user or flask.g.user.age < app.config['ACCOUNT_RECAPTCHA_AGE']:
            return RecaptchaValidator()(form, field)
    else:
        # Always pass validating the recaptcha field if disabled
        return True


def register_email_blacklist_validator(form, field):
    email_blacklist = app.config.get('EMAIL_BLACKLIST', [])
    email = field.data.strip()
    validation_exception = StopValidation('Blacklisted email provider')

    for item in email_blacklist:
        if isinstance(item, re.Pattern):
            if item.search(email):
                raise validation_exception
        elif isinstance(item, str):
            if item in email.lower():
                raise validation_exception
        else:
            raise Exception('Unexpected email validator type {!r} ({!r})'.format(type(item), item))
    return True


def register_email_server_validator(form, field):
    server_blacklist = app.config.get('EMAIL_SERVER_BLACKLIST', [])
    if not server_blacklist:
        return True

    validation_exception = StopValidation('Blacklisted email provider')
    email = field.data.strip()
    email_domain = email.split('@', 1)[-1]

    try:
        # Query domain MX records
        mx_records = list(dns.resolver.query(email_domain, 'MX'))

    except dns.exception.DNSException:
        app.logger.error('Unable to query MX records for email: %s - ignoring',
                         email, exc_info=False)
        return True

    for mx_record in mx_records:
        try:
            # Query mailserver A records
            a_records = list(dns.resolver.query(mx_record.exchange))
            for a_record in a_records:
                # Check for address in blacklist
                if a_record.address in server_blacklist:
                    app.logger.warning('Rejected email %s due to blacklisted mailserver (%s, %s)',
                                       email, a_record.address, mx_record.exchange)
                    raise validation_exception

        except dns.exception.DNSException:
            app.logger.warning('Failed to query A records for mailserver: %s (%s) - ignoring',
                               mx_record.exchange, email, exc_info=False)

    return True


_username_validator = Regexp(
    r'^[a-zA-Z0-9_\-]+$',
    message='Your username must only consist of alphanumerics and _- (a-zA-Z0-9_-)')


class LoginForm(FlaskForm):
    username = StringField('Username or email address', [DataRequired()])
    password = PasswordField('Password', [DataRequired()])


class PasswordResetRequestForm(FlaskForm):
    email = StringField('Email address', [
        Email(),
        DataRequired(),
        Length(min=5, max=128)
    ])

    recaptcha = RecaptchaField(validators=[recaptcha_validator_shim])


class PasswordResetForm(FlaskForm):
    password = PasswordField('Password', [
        DataRequired(),
        EqualTo('password_confirm', message='Passwords must match'),
        Length(min=6, max=1024,
               message='Password must be at least %(min)d characters long.')
    ])

    password_confirm = PasswordField('Password (confirm)')


class RegisterForm(FlaskForm):
    username = StringField('Username', [
        DataRequired(),
        Length(min=3, max=32),
        stop_on_validation_error(_username_validator),
        Unique(User, User.username, 'Username not available')
    ])

    email = StringField('Email address', [
        Email(),
        DataRequired(),
        Length(min=5, max=128),
        register_email_blacklist_validator,
        Unique(User, User.email, 'Email already in use by another account'),
        register_email_server_validator
    ])

    password = PasswordField('Password', [
        DataRequired(),
        EqualTo('password_confirm', message='Passwords must match'),
        Length(min=6, max=1024,
               message='Password must be at least %(min)d characters long.')
    ])

    password_confirm = PasswordField('Password (confirm)')

    if config['USE_RECAPTCHA']:
        recaptcha = RecaptchaField()


class ProfileForm(FlaskForm):
    email = StringField('New Email Address', [
        Email(),
        Optional(),
        Length(min=5, max=128),
        Unique(User, User.email, 'This email address has been taken')
    ])

    current_password = PasswordField('Current Password', [DataRequired()])

    new_password = PasswordField('New Password', [
        Optional(),
        EqualTo('password_confirm', message='Two passwords must match'),
        Length(min=6, max=1024,
               message='Password must be at least %(min)d characters long.')
    ])

    password_confirm = PasswordField('Repeat New Password')
    hide_comments = BooleanField('Hide comments by default')

    authorized_submit = SubmitField('Update')
    submit_settings = SubmitField('Update')


# Classes for a SelectField that can be set to disable options (id, name, disabled)
# TODO: Move to another file for cleaner look
class DisabledSelectWidget(SelectWidget):
    def __call__(self, field, **kwargs):
        kwargs.setdefault('id', field.id)
        if self.multiple:
            kwargs['multiple'] = True
        html = ['<select %s>' % html_params(name=field.name, **kwargs)]
        for val, label, selected, disabled in field.iter_choices():
            extra = disabled and {'disabled': ''} or {}
            html.append(self.render_option(val, label, selected, **extra))
        html.append('</select>')
        return HTMLString(''.join(html))


class DisabledSelectField(SelectField):
    widget = DisabledSelectWidget()

    def iter_choices(self):
        for choice_tuple in self.choices:
            value, label = choice_tuple[:2]
            disabled = len(choice_tuple) == 3 and choice_tuple[2] or False
            yield (value, label, self.coerce(value) == self.data, disabled)

    def pre_validate(self, form):
        for v in self.choices:
            if self.data == v[0]:
                break
        else:
            raise ValueError(self.gettext('Not a valid choice'))


class CommentForm(FlaskForm):
    comment = TextAreaField('Make a comment', [
        Length(min=3, max=2048, message='Comment must be at least %(min)d characters '
               'long and %(max)d at most.'),
        DataRequired(message='Comment must not be empty.')
    ])

    recaptcha = RecaptchaField(validators=[upload_recaptcha_validator_shim])


class InlineButtonWidget(object):
    """
    Render a basic ``<button>`` field.
    """
    input_type = 'submit'
    html_params = staticmethod(html_params)

    def __call__(self, field, label=None, **kwargs):
        kwargs.setdefault('id', field.id)
        kwargs.setdefault('type', self.input_type)
        if not label:
            label = field.label.text
        return HTMLString('<button %s>' % self.html_params(name=field.name, **kwargs) + label)


class StringSubmitField(StringField):
    """
    Represents an ``<button type="submit">``.  This allows checking if a given
    submit button has been pressed.
    """
    widget = InlineButtonWidget()


class StringSubmitForm(FlaskForm):
    submit = StringSubmitField('Submit')


class EditForm(FlaskForm):
    display_name = StringField('Item display name', [
        Length(min=3, max=255, message='Item display name must be at least %(min)d characters '
               'long and %(max)d at most.')
    ])

    category = DisabledSelectField('Category')

    def validate_category(form, field):
        cat_match = re.match(r'^(\d+)_(\d+)$', field.data)
        if not cat_match:
            raise ValidationError('Please select a category')

        main_cat_id = int(cat_match.group(1))
        sub_cat_id = int(cat_match.group(2))

        cat = models.SubCategory.by_category_ids(main_cat_id, sub_cat_id)

        if not cat:
            raise ValidationError('Please select a proper category')

        field.parsed_data = cat

    is_hidden = BooleanField('Hidden')
    is_remake = BooleanField('Remake')
    is_anonymous = BooleanField('Anonymous')
    is_complete = BooleanField('Complete')
    is_trusted = BooleanField('Trusted')
    is_comment_locked = BooleanField('Lock Comments')

    information = StringField('Information', [
        Length(max=255, message='Information must be at most %(max)d characters long.')
    ])
    description = TextAreaField('Description', [
        Length(max=10 * 1024, message='Description must be at most %(max)d characters long.')
    ])

    submit = SubmitField('Save Changes')


class DeleteForm(FlaskForm):
    delete = SubmitField("Delete")
    ban = SubmitField("Delete & Ban")
    undelete = SubmitField("Undelete")
    unban = SubmitField("Unban")


class BanForm(FlaskForm):
    ban_user = SubmitField("Delete & Ban and Ban User")
    ban_userip = SubmitField("Delete & Ban and Ban User+IP")
    unban = SubmitField("Unban")

    _validator = DataRequired()

    def _validate_reason(form, field):
        if form.ban_user.data or form.ban_userip.data:
            return BanForm._validator(form, field)

    reason = TextAreaField('Ban Reason', [
        _validate_reason,
        Length(max=1024, message='Reason must be at most %(max)d characters long.')
    ])


class NukeForm(FlaskForm):
    nuke_items = SubmitField("\U0001F4A3 Nuke Items")
    nuke_comments = SubmitField("\U0001F4A3 Nuke Comments")


class UploadForm(FlaskForm):
    submission_file = FileField('Item file', [
        FileRequired()
    ])

    display_name = StringField('Item display name', [
        Length(min=3, max=255,
               message='Item display name must be at least %(min)d characters long and '
                       '%(max)d at most.')
    ])

    recaptcha = RecaptchaField(validators=[upload_recaptcha_validator_shim])

    category = DisabledSelectField('Category')

    def validate_category(form, field):
        cat_match = re.match(r'^(\d+)_(\d+)$', field.data)
        if not cat_match:
            raise ValidationError('Please select a category')

        main_cat_id = int(cat_match.group(1))
        sub_cat_id = int(cat_match.group(2))

        cat = models.SubCategory.by_category_ids(main_cat_id, sub_cat_id)

        if not cat:
            raise ValidationError('Please select a proper category')

        field.parsed_data = cat

    is_hidden = BooleanField('Hidden')
    is_remake = BooleanField('Remake')
    is_anonymous = BooleanField('Anonymous')
    is_complete = BooleanField('Complete')
    is_trusted = BooleanField('Trusted')
    is_comment_locked = BooleanField('Lock Comments')

    information = StringField('Information', [
        Length(max=255, message='Information must be at most %(max)d characters long.')
    ])
    description = TextAreaField('Description', [
        Length(max=10 * 1024, message='Description must be at most %(max)d characters long.')
    ])

    ratelimit = HiddenField()
    rangebanned = HiddenField()

    def validate_submission_file(form, field):
        return True
        handler = get_handler_by_mimetype(field.data.mimetype)
        handler.validate_upload(form, field)

class UserForm(FlaskForm):
    user_class = SelectField('Change User Class')
    activate_user = SubmitField('Activate User')

    def validate_user_class(form, field):
        if not field.data:
            raise ValidationError('Please select a proper user class')


class ReportForm(FlaskForm):
    reason = TextAreaField('Report reason', [
        Length(min=3, max=255,
               message='Report reason must be at least %(min)d characters long '
                       'and %(max)d at most.'),
        DataRequired('You must provide a valid report reason.')
    ])


class ReportActionForm(FlaskForm):
    action = SelectField(choices=[('close', 'Close'), ('hide', 'Hide'), ('delete', 'Delete')])
    item = HiddenField()
    report = HiddenField()


class TrustedForm(FlaskForm):
    why_give_trusted = TextAreaField('Why do you think you should be given trusted status?', [
        Length(min=32, max=4000,
               message='Please explain why you think you should be given trusted status in at '
                       'least %(min)d but less than %(max)d characters.'),
        DataRequired('Please fill out all of the fields in the form.')
    ])
    why_want_trusted = TextAreaField('Why do you want to become a trusted user?', [
        Length(min=32, max=4000,
               message='Please explain why you want to become a trusted user in at least %(min)d '
                       'but less than %(max)d characters.'),
        DataRequired('Please fill out all of the fields in the form.')
    ])


class TrustedReviewForm(FlaskForm):
    comment = TextAreaField('Comment',
                            [Length(min=8, max=4000, message='Please provide a comment')])
    recommendation = SelectField(choices=[('abstain', 'Abstain'), ('reject', 'Reject'),
                                          ('accept', 'Accept')])


class TrustedDecisionForm(FlaskForm):
    accept = SubmitField('Accept')
    reject = SubmitField('Reject')

