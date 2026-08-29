# Developer access

## Opening the Developer tab in the app

1. Go to the **More** tab.
2. Below the disclaimer text, click the small muted **Developer** link. It reveals
   a username / password field.
3. Enter the credentials below and click **Open Developer Tab**. The Developer tab
   is added to the sidebar and stays for the rest of the session.

## Credentials

    username: dev
    password: password

Stored in `core/dev_access.py` as a PBKDF2-HMAC-SHA256 hash with a random 16-byte
salt and 200,000 iterations (`_DEV_PW_SALT` / `_DEV_PW_HASH` / `_DEV_PW_ITERS`) -
not plaintext. Granted accounts (see the "grant access" panel in the Developer
tab) are stored the same way, one random salt per entry, in
`%APPDATA%\Media Downloader\options\dev_access.json`.

## Changing the built-in password

Run this one-liner, then paste the three printed values into `_DEV_PW_SALT`,
`_DEV_PW_HASH`, and `_DEV_PW_ITERS` in `core/dev_access.py`:

    py -3 -c "import secrets,hashlib; s=secrets.token_bytes(16); i=200000; print('salt=',s.hex()); print('hash=',hashlib.pbkdf2_hmac('sha256', input('new password: ').encode(), s, i).hex()); print('iters=',i)"

The username is the plain `DEV_USERNAME` constant in the same file.

## Note

The repository must stay **private** - even though the password is now hashed, the
hash + salt + iteration count are all in the source, so anyone with the repo can
brute-force a weak password offline.
