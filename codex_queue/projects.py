"""Private, in-memory navigation catalog. Never walks project files or auth data."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sqlite3
from .sessions import identifier
from .store import QueueError, git


@dataclass
class Conversation:
    id: str
    cwd: str
    title: str
    source: str
    activity: float
    preview: str = ''
    available: bool = True


@dataclass
class Project:
    path: str
    name: str
    conversations: list = field(default_factory=list)
    @property
    def activity(self): return max((c.activity for c in self.conversations), default=0)


_metadata_cache = None
_metadata_cache_time = 0
_override_metadata = None


def set_mock_metadata(metadata):
    """Set mock metadata for isolated testing without reading local files."""
    global _override_metadata, _metadata_cache
    _override_metadata = metadata
    _metadata_cache = metadata


def sanitize_chat_title(title: str | None, max_length: int = 58) -> str:
    """Sanitize a chat title: take ONLY first non-empty line, strip extra whitespace,
    and truncate cleanly with ellipsis to max 50-60 characters.
    """
    if not title:
        return "Untitled chat"
    lines = [line.strip() for line in str(title).splitlines() if line.strip()]
    if not lines:
        return "Untitled chat"
    first_line = " ".join(lines[0].split())
    if len(first_line) > max_length:
        return first_line[:max_length - 1].rstrip() + "…"
    return first_line


def _norm_path_str(p: str) -> str:
    s = str(p).replace('\\', '/').rstrip('/')
    if len(s) >= 2 and s[1] == ':':
        s = s[2:]
    if os.name == 'nt':
        s = s.lower()
    return s


def _is_current_user_path(path_str: str) -> bool:
    try:
        p = Path(path_str)
        if p.exists() and p.resolve().is_relative_to(Path.home().resolve()):
            return True
    except Exception:
        pass
    home_name = Path.home().name.lower()
    norm = str(path_str).replace('\\', '/').lower()
    for prefix in ('/users/', '/home/'):
        if prefix in norm:
            user = norm.split(prefix, 1)[1].split('/', 1)[0]
            if user == home_name:
                return True
            if '~' in user and home_name.startswith(user.split('~')[0]):
                return True
            if '~' in home_name and user.startswith(home_name.split('~')[0]):
                return True
    return False


def _path_aliases(path_str: str) -> list[str]:
    """Return aliases for a path, remapping to current user's home if from another user home."""
    try:
        if _is_current_user_path(path_str):
            aliases = [path_str]
            slash_path = path_str.replace('\\', '/')
            if slash_path not in aliases:
                aliases.append(slash_path)
            try:
                resolved = str(Path(path_str).resolve())
                if resolved not in aliases:
                    aliases.append(resolved)
                resolved_slash = resolved.replace('\\', '/')
                if resolved_slash not in aliases:
                    aliases.append(resolved_slash)
            except Exception:
                pass
            return aliases
        norm = str(path_str).replace('\\', '/')
        home = Path.home()
        m = re.match(r'^(?:[A-Za-z]:)?/(Users|home)/([^/]+)(?:/(.*))?$', norm)
        if m:
            sub = m.group(3) or ""
            target = str(home / Path(sub)) if sub else str(home)
            return [target]
    except Exception:
        pass
    return [path_str]


def _remap_to_current_home(path_str: str) -> str:
    """If path is under another user's home, remap to current user's home."""
    if not isinstance(path_str, str):
        return path_str
    if _is_current_user_path(path_str):
        return path_str
    try:
        norm = str(path_str).replace('\\', '/')
        home = Path.home()
        m = re.match(r'^(?:[A-Za-z]:)?/(Users|home)/([^/]+)(?:/(.*))?$', norm)
        if m:
            sub = m.group(3) or ""
            target = str(home / Path(sub)) if sub else str(home)
            return target
    except Exception:
        pass
    return path_str




def load_codex_metadata(force=False):
    """Load project names, roots, and threads from local Codex databases/config."""
    global _metadata_cache, _metadata_cache_time, _override_metadata
    if _override_metadata is not None:
        return _override_metadata

    import time
    now = time.time()
    if not force and _metadata_cache is not None and now - _metadata_cache_time < 30:
        return _metadata_cache

    projects_by_path = {}
    projects_by_id = {}
    threads_metadata = []

    # Locate ~/.codex directory across possible HOME configurations
    codex_dir = None
    import os
    candidates = []
    if "CODEX_HOME" in os.environ:
        candidates.append(Path(os.environ["CODEX_HOME"]))
    candidates.append(Path.home() / ".codex")
    for cand in candidates:
        try:
            if cand.exists():
                codex_dir = cand
                break
        except OSError:
            pass

    if codex_dir is not None:
        # 1. state_5.sqlite
        db = codex_dir / "state_5.sqlite"
        try:
            if db.exists():
                con = sqlite3.connect(db)
                con.row_factory = sqlite3.Row
                for r in con.execute("SELECT id, name FROM projects").fetchall():
                    projects_by_id[r["id"]] = r["name"]
                for r in con.execute("SELECT project_id, path FROM project_roots").fetchall():
                    pname = projects_by_id.get(r["project_id"])
                    if pname and r["path"]:
                        p_resolved = str(Path(r["path"]).resolve())
                        for alias in _path_aliases(p_resolved):
                            projects_by_path[alias] = pname
                cols = {c[1] for c in con.execute("PRAGMA table_info(threads)").fetchall()}
                if "id" in cols and "cwd" in cols:
                    query = "SELECT id, cwd, title, name, created_at, updated_at, preview FROM threads ORDER BY updated_at DESC LIMIT 500"
                    for row in con.execute(query).fetchall():
                        threads_metadata.append(dict(row))
                con.close()
        except Exception:
            pass

        # 2. .codex-global-state.json
        gs = codex_dir / ".codex-global-state.json"
        try:
            if gs.exists():
                with open(gs) as f:
                    data = json.load(f)
                for pid, p in data.get("local-projects", {}).items():
                    name = p.get("name")
                    for root in p.get("rootPaths", []):
                        r_resolved = str(Path(root).resolve())
                        for alias in _path_aliases(r_resolved):
                            projects_by_path[alias] = name
                atom = data.get("electron-persisted-atom-state", {})
                for acc in (atom.get("chatgpt-sidebar-state-v2") or atom.get("chatgpt-sidebar-state-v1") or {}).values():
                    for p in acc.get("projects", []):
                        if p.get("name"):
                            projects_by_id[p.get("id")] = p.get("name")
        except Exception:
            pass

    # 3. Resolve any unmapped projects by checking Developer folder and threads cwd
    dev_dirs = [Path.home() / "Developer"]
    for pid, pname in projects_by_id.items():
        found = any(pname.lower() == name.lower() for name in projects_by_path.values())
        if not found:
            for d in dev_dirs:
                try:
                    if not d.exists():
                        continue
                    for candidate in [d / f"{pname}.git", d / f"{pname.lower()}.git", d / pname, d / pname.lower()]:
                        if candidate.exists():
                            c_resolved = str(candidate.resolve())
                            projects_by_path[c_resolved] = pname
                            found = True
                            break
                    if found:
                        break
                except OSError:
                    pass
        if not found:
            clean_name = "".join(c for c in pname if c.isalnum()).lower()
            for t in threads_metadata:
                cwd = t.get("cwd") or ""
                if clean_name and clean_name in cwd.lower():
                    try:
                        p = Path(cwd)
                        while p != p.parent:
                            if (p / ".git").exists() or p.name.endswith(".git"):
                                projects_by_path[str(p.resolve())] = pname
                                found = True
                                break
                            p = p.parent
                    except Exception:
                        pass
                    if found:
                        break

    _metadata_cache = (projects_by_path, projects_by_id, threads_metadata)
    _metadata_cache_time = now
    return _metadata_cache



def resolve_project_name(path):
    if not path:
        return "Unknown Project"
    path_str = str(path)
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        resolved = path_str
    projects_by_path, _, _ = load_codex_metadata()
    if path_str in projects_by_path:
        return projects_by_path[path_str]
    if resolved in projects_by_path:
        return projects_by_path[resolved]
    norm_resolved = _norm_path_str(resolved)
    norm_path = _norm_path_str(path_str)
    for root, name in projects_by_path.items():
        norm_root = _norm_path_str(root)
        if norm_resolved == norm_root or norm_path == norm_root:
            return name
        if norm_resolved.startswith(norm_root + "/") or norm_root.startswith(norm_resolved + "/"):
            return name
        if norm_path.startswith(norm_root + "/") or norm_root.startswith(norm_path + "/"):
            return name
    name = Path(resolved).name
    if name.endswith(".git"):
        name = name[:-4]
    return name



def resolve_chat_title(session_id):
    if not session_id:
        return "New Session"
    try:
        sid = identifier(session_id)
    except Exception:
        sid = str(session_id)
    _, _, threads = load_codex_metadata()
    for t in threads:
        if t.get("id") == sid:
            raw = t.get("name") or t.get("title") or t.get("preview") or sid
            if raw == sid:
                return sid
            return sanitize_chat_title(raw)
    return sid


class ProjectsDict(dict):
    def _find_key(self, key):
        if super().__contains__(key):
            return key
        norm_key = _norm_path_str(key)
        for k in self:
            if _norm_path_str(k) == norm_key:
                return k
        try:
            res = str(Path(key).resolve())
            if super().__contains__(res):
                return res
            norm_res = _norm_path_str(res)
            for k in self:
                if _norm_path_str(k) == norm_res:
                    return k
        except Exception:
            pass
        return None

    def __contains__(self, key):
        return self._find_key(key) is not None

    def __getitem__(self, key):
        k = self._find_key(key)
        if k is not None:
            return super().__getitem__(k)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        k = self._find_key(key)
        if k is not None:
            super().__setitem__(k, value)
        else:
            super().__setitem__(key, value)

    def __delitem__(self, key):
        k = self._find_key(key)
        if k is not None:
            super().__delitem__(k)
        else:
            super().__delitem__(key)

    def get(self, key, default=None):
        k = self._find_key(key)
        if k is not None:
            return super().__getitem__(k)
        return default

    def setdefault(self, key, default=None):
        k = self._find_key(key)
        if k is not None:
            return super().__getitem__(k)
        super().__setitem__(key, default)
        return default

    def pop(self, key, *args):
        k = self._find_key(key)
        if k is not None:
            return super().pop(k)
        if args:
            return args[0]
        return super().pop(key)


class Catalog:
    def __init__(self): self.projects = ProjectsDict()
    def manual(self, repo):
        root = str(Path(git(Path(repo).expanduser(), 'rev-parse', '--show-toplevel')).resolve())
        name = resolve_project_name(root)
        return self.projects.setdefault(root, Project(root, name))

    def discover_local_codex(self):
        projects_by_path, _, threads = load_codex_metadata()
        home_str = str(Path.home())
        for raw_path, name in projects_by_path.items():
            path_str = _remap_to_current_home(raw_path)
            if path_str.startswith("/Users/") or path_str.startswith("/home/"):
                if not (path_str == home_str or path_str.startswith(home_str + "/")):
                    continue
            if _override_metadata is None:
                try:
                    if not Path(path_str).exists():
                        continue
                except OSError:
                    continue
            if path_str not in self.projects:
                self.projects[path_str] = Project(path_str, name)
            elif self.projects[path_str].name == Path(path_str).name:
                self.projects[path_str].name = name

        records = []
        for t in threads:
            records.append({
                'id': t['id'],
                'cwd': t.get('cwd') or '',
                'title': sanitize_chat_title(t.get('name') or t.get('title') or t.get('preview') or 'Untitled chat'),
                'source': 'codex',
                'preview': t.get('preview') or '',
                'updatedAt': t.get('updated_at') or t.get('created_at') or 0,
                'status': {'type': 'idle'}
            })
        if records:
            self.ingest(records)

        # Filter out projects with zero activity/conversations
        self.projects = ProjectsDict({path: p for path, p in self.projects.items() if p.conversations})


    def ingest(self, records):
        # Group metadata by reported project root/cwd without probing any checkout.
        for item in records:
            try: sid = identifier(item.get('id'))
            except QueueError: continue
            cwd = item.get('cwd')
            if not isinstance(cwd, str): continue
            is_cwd_abs = Path(cwd).is_absolute() or cwd.startswith('/') or cwd.startswith('\\') or (len(cwd) >= 3 and cwd[1:3] in (':/', ':\\'))
            if not is_cwd_abs: continue
            cwd = _remap_to_current_home(cwd)
            info = item.get('gitInfo') or {}
            root = info.get('repositoryRoot') or item.get('projectPath') or cwd
            if not isinstance(root, str): root = cwd
            is_root_abs = Path(root).is_absolute() or root.startswith('/') or root.startswith('\\') or (len(root) >= 3 and root[1:3] in (':/', ':\\'))
            if not is_root_abs: root = cwd
            root = _remap_to_current_home(root)
            
            # Match against existing projects
            norm_root = _norm_path_str(root)
            for p_path in self.projects:
                norm_p = _norm_path_str(p_path)
                if norm_root == norm_p or norm_root.startswith(norm_p + "/") or norm_p.startswith(norm_root + "/"):
                    root = p_path
                    break
            
            p_name = resolve_project_name(root)
            if root not in self.projects:
                self.projects[root] = Project(root, p_name)
            elif self.projects[root].name == Path(root).name:
                self.projects[root].name = p_name
            project = self.projects[root]
            source = item.get('source', 'unknown')
            if isinstance(source, dict): source = next(iter(source), 'unknown')
            activity = item.get('updatedAt', item.get('createdAt', 0))
            if not isinstance(activity, (int, float)): activity = 0
            clean_title = sanitize_chat_title(item.get('name') or item.get('title') or item.get('preview') or 'Untitled conversation')
            conversation = Conversation(sid, cwd, clean_title, str(source), activity, str(item.get('preview') or '')[:4000], item.get('status', {}).get('type') in ('idle', 'notLoaded'))
            project.conversations = [c for c in project.conversations if c.id != sid] + [conversation]
    def filtered(self, project, text=''):
        text=text.casefold()
        return sorted((c for c in project.conversations if text in (c.title+' '+c.id+' '+c.source).casefold()), key=lambda c:c.activity, reverse=True)

