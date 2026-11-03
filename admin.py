"""Owner's review console for the knowledge base.

    python admin.py stats                     # health of the knowledge loop
    python admin.py pending                   # unanswered questions, most-asked first
    python admin.py approve 12 "My answer"    # answer question 12 -> approved Q&A pair
    python admin.py dismiss 12                # off-topic / spam
    python admin.py add "Question?" "Answer"  # write an answer proactively
    python admin.py list                      # approved answers and usage
    python admin.py edit 3 "Better answer"    # revise an approved answer
    python admin.py archive 3                 # retire an answer
    python admin.py contacts                  # people who asked to get in touch
    python admin.py export                    # write approved answers to the seed file
    python admin.py search "question"         # see what the twin would retrieve
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from dotenv import load_dotenv

from twin import load_config
from twin.knowledge import open_knowledge_base


# Squeeze long text onto one short line for terminal output.
def _wrap(text: str, width: int = 90) -> str:
    return textwrap.shorten(text.replace("\n", " "), width=width, placeholder="...")


def main(argv: list[str] | None = None) -> int:
    # Load environment variables in case the config depends on them.
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Review and curate the twin's knowledge base")
    parser.add_argument("--config", help="Config file (defaults to TWIN_CONFIG or config/twin.yaml)")
    # One subcommand per task; the module docstring above lists them all.
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats")
    sub.add_parser("pending")
    p = sub.add_parser("approve"); p.add_argument("id", type=int); p.add_argument("answer")
    p.add_argument("--question", help="Reword the stored question (e.g. fix typos)")
    p = sub.add_parser("dismiss"); p.add_argument("id", type=int)
    p = sub.add_parser("add"); p.add_argument("question"); p.add_argument("answer")
    p = sub.add_parser("list"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("edit"); p.add_argument("id", type=int); p.add_argument("answer")
    p = sub.add_parser("archive"); p.add_argument("id", type=int)
    sub.add_parser("contacts")
    p = sub.add_parser("export"); p.add_argument("--path")
    p = sub.add_parser("import-emails"); p.add_argument("path")
    p = sub.add_parser("search"); p.add_argument("query")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    # Opens the same database as the app, so with Turso set up these commands change live data.
    kb = open_knowledge_base(cfg)

    # Each branch below handles one subcommand.
    if args.cmd == "stats":
        for k, v in kb.stats().items():
            print(f"{k:20} {v}")
    elif args.cmd == "pending":
        rows = kb.pending_questions()
        if not rows:
            print("No pending questions.")
        for r in rows:
            print(f"[{r['id']:>4}] x{r['ask_count']:<3} {_wrap(r['question'])}  (last {r['last_asked_at'][:10]})")
    # Turns a pending question into an approved answer the twin can use.
    elif args.cmd == "approve":
        qa_id = kb.approve_question(args.id, args.answer, args.question)
        print(f"Approved. Question {args.id} -> Q&A pair {qa_id}. Run `python admin.py export` to persist it in git.")
    elif args.cmd == "dismiss":
        kb.dismiss_question(args.id)
        print(f"Dismissed question {args.id}.")
    elif args.cmd == "add":
        print(f"Added Q&A pair {kb.add_qa(args.question, args.answer)}.")
    elif args.cmd == "list":
        for r in kb.list_qa(include_archived=args.all):
            print(f"[{r['id']:>4}] used {r['times_used']:<3} {r['status']:<8} Q: {_wrap(r['question'], 70)}")
            print(f"{'':>6}A: {_wrap(r['answer'], 80)}")
    elif args.cmd == "edit":
        kb.update_qa(args.id, args.answer)
        print(f"Updated Q&A pair {args.id}.")
    elif args.cmd == "archive":
        kb.archive_qa(args.id)
        print(f"Archived Q&A pair {args.id}.")
    elif args.cmd == "contacts":
        for r in kb.list_contacts():
            print(f"{r['created_at'][:10]}  {r['email']:<35} {r['name'] or '-':<20} {_wrap(r['notes'] or '', 50)}")
    # Saves approved answers to the seed file so they can be committed and survive redeploys.
    elif args.cmd == "export":
        path = args.path or cfg.knowledge_base.seed_file or "data/approved_answers.yaml"
        print(f"Exported {kb.export_yaml(cfg.resolve(path))} approved answers to {path}.")
    elif args.cmd == "import-emails":
        with open(args.path, encoding="utf-8") as f:
            emails = {line.strip() for line in f if line.strip()}
        for e in emails:
            kb.record_contact(e, notes="Imported from emails.txt")
        print(f"Imported {len(emails)} unique contacts.")
    # Threshold 0 shows every candidate, flagged by whether the twin would actually use it.
    elif args.cmd == "search":
        matches = kb.search(args.query, threshold=0.0, limit=5)
        threshold = cfg.knowledge_base.match_threshold
        for m in matches:
            flag = "USED   " if m.score >= threshold else "ignored"
            print(f"{flag} score={m.score:.2f} [{m.id}] {_wrap(m.question, 70)}")
        if not matches:
            print("No candidates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
