# Small script that lets you evaluate users to any role you want. You need their ID.

from tsuu.extensions import db

from tsuu import create_app, models
import sys

app = create_app('config')

if __name__ == "__main__":
    with app.app_context():
        if len(sys.argv) == 2:
            print("""Syntax: promote_user.py <user id> <role>
You can find user id in the web interface.

Valid values for role are:

0 = Regular user
1 = Trusted user
2 = Moderator
3 = Superadmin""")
            exit(1)
        q = db.session.query(models.User).filter_by(id=sys.argv[1]).one()
        q.level = models.UserLevelType(int(sys.argv[2]))
        db.session.merge(q)
        db.session.commit()
        print(f"Successfully promoted user {q.username} ({q.id}) to {models.UserLevelType(int(sys.argv[2])).name}")
