"""Generate an Argon2id password hash locally; no plaintext password in arguments."""

from getpass import getpass

from pwdlib import PasswordHash


def main():
    password = getpass("Mot de passe (12 caracteres minimum) : ")
    if not 12 <= len(password) <= 1024:
        raise SystemExit("Utilisez entre 12 et 1024 caracteres.")
    if password != getpass("Confirmez le mot de passe : "):
        raise SystemExit("Les mots de passe different.")
    print(PasswordHash.recommended().hash(password))


if __name__ == "__main__":
    main()
