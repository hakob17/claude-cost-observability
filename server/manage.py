"""Admin CLI for the cost-observability server.

Dashboard login is the single admin user, configured via environment:
    COST_OBS_ADMIN_PASSWORD   required — enables login
    COST_OBS_ADMIN_USER       optional — username (default: admin)

Ingest tokens can also be generated from the dashboard (Tokens panel).

    python3 manage.py init                      create the database
    python3 manage.py add-token <name>          create an ingest token for the plugins
    python3 manage.py add-token <name> --admin  create an admin API token (full read access)
    python3 manage.py list                      show tokens
"""

import argparse
import time

import db


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    t = sub.add_parser("add-token")
    t.add_argument("name")
    t.add_argument("--admin", action="store_true")
    sub.add_parser("list")
    args = p.parse_args()

    db.init_db()
    if args.cmd == "init":
        print(f"Database ready at {db.DB_PATH}")
    elif args.cmd == "add-token":
        token = db.create_api_token(args.name, role="admin" if args.admin else "ingest")
        print(f"{'Admin' if args.admin else 'Ingest'} token for '{args.name}' "
              f"(store it now — it is not shown again):\n\n  {token}\n")
    elif args.cmd == "list":
        print("API tokens:")
        for r in db.list_tokens():
            print(f"  {r['name']:<24} {r['role']:<7} {r['prefix']}…  "
                  f"created {time.strftime('%Y-%m-%d', time.localtime(r['created_at']))}")


if __name__ == "__main__":
    main()
