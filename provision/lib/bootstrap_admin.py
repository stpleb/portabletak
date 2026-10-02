"""Initialize the first administrator in an already migrated, empty OTS DB.

The caller must establish and verify the isolated clean database connection.
This helper never resets an existing account and never starts an OTS listener.
"""

def initialize_administrator(app, password):
    from flask_security import hash_password, verify_password
    from opentakserver.extensions import db

    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise ValueError('invalid_administrator_password')
    with app.app_context():
        datastore = app.security.datastore
        if db.session.query(datastore.user_model).count() != 0:
            raise ValueError('database_not_empty')
        datastore.find_or_create_role(name='user', permissions={'user-read', 'user-write'})
        datastore.find_or_create_role(name='administrator', permissions={'administrator'})
        user = datastore.create_user(username='administrator', password=hash_password(password),
                                     active=True, roles=['administrator'])
        if not verify_password(password, user.password) or verify_password('password', user.password):
            db.session.rollback()
            raise ValueError('administrator_password_verification_failed')
        db.session.commit()
        return {'ok': True, 'administrator_created': True}
