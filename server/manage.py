"""Admin CLI for the cost-observability server.

    python3 manage.py init                      create the database
    python3 manage.py add-user <username>       create/replace a dashboard user (prompts password)
    python3 manage.py add-token <name>          create an ingest token for the plugin
    python3 manage.py add-token <name> --admin  create an admin API token (full read access)
    python3 manage.py list                      show users and tokens
"""

import argparse
import getpass
import time

import db


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add-user")
    a.add_argument("username")
    t = sub.add_parser("add-token")
    t.add_argument("name")
    t.add_argument("--admin", action="store_true")
    sub.add_parser("list")
    args = p.parse_args()

    db.init_db()
    if args.cmd == "init":
        print(f"Database ready at {db.DB_PATH}")
    elif args.cmd == "add-user":
        pw = getpass.getpass(f"Password for {args.username}: ")
        pw2 = getpass.getpass("Repeat: ")
        if pw != pw2 or not pw:
            raise SystemExit("Passwords empty or do not match.")
        db.add_user(args.username, pw)
        print(f"User '{args.username}' created.")
    elif args.cmd == "add-token":
        token = db.create_api_token(args.name, role="admin" if args.admin else "ingest")
        print(f"{'Admin' if args.admin else 'Ingest'} token for '{args.name}' "
              f"(store it now — it is not shown again):\n\n  {token}\n")
    elif args.cmd == "list":
        with db.connect() as conn:
            print("Users:")
            for r in conn.execute("SELECT username, created_at FROM users"):
                print(f"  {r['username']:<24} created {time.strftime('%Y-%m-%d', time.localtime(r['created_at']))}")
            print("API tokens:")
            for r in conn.execute("SELECT name, role, token, created_at FROM api_tokens"):
                print(f"  {r['name']:<24} {r['role']:<7} {r['token'][:12]}…")


if __name__ == "__main__":
    main()
