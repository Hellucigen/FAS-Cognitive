"""Read-only audit helper: which modules touch LLM/companion-side deps (incl. lazy in-function imports)."""
import ast, os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
suspects = {"nlp_processor", "prompt_templates", "llm_provider", "ollama_backend", "app"}
skip_dirs = {".git", "__pycache__", ".pytest_cache", ".idea", "data", "logs", "Dataset",
             "legacy", "node_modules", "FAS_Paper_I", "PeerJ", "deliverables", "paper_audit",
             "research_audit", "experiments", "FAS_Research_Experiments",
             "experiment_dynamic_revalidation", "reports", "Try", "scripts"}
report = {}
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d not in skip_dirs]
    for f in fn:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if rel == "app.py":
            continue
        try:
            t = ast.parse(open(p, encoding="utf-8", errors="replace").read())
        except Exception:
            continue
        hits = set()
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                names = [n.module or ""]
            else:
                continue
            for c in names:
                top = c.split(".")[0]
                if top in suspects:
                    hits.add((top, n.lineno))
        if hits:
            report[rel] = sorted(hits)
for k, v in report.items():
    print(k, "->", v)
