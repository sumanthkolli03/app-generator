"""Checks for the generator, on throwaway server folders. No Docker needed; with docker compose
installed, the rendered files also go through `docker compose config`, and with CADDY_BIN set
(a caddy binary) through `caddy validate`.
    python3 -I tests/test_apps.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import apps  # noqa: E402

PASSED, FAILED = 0, []


def check(name, ok):
    global PASSED
    if ok:
        PASSED += 1
    else:
        FAILED.append(name)
        print("FAIL", name)


BASE = """services:
  wg-public:
    image: x
    networks: [public_net]
  caddy-public:
    image: caddy:latest
    network_mode: "service:wg-public"
    environment:
      - PUBLIC_DOMAIN=${PUBLIC_DOMAIN:?set PUBLIC_DOMAIN in .env}
  jellyfin:
    image: x
networks:
  public_net: {}
"""
CADDYFILE = "{\n    admin off\n}\n\nimport apps/*.caddy\n"
ENV = "PUBLIC_DOMAIN=p.example.net\nPRIVATE_DOMAIN=m.example.org\nPRIVATE_AUTH_DOMAIN=auth.m.example.org\n" \
      "COMPOSE_FILE=docker-compose.yml:generated/docker-compose.apps.yml\n"
STATIC = """kind = "static"
[sites.main]
headers = { Content-Security-Policy = "default-src 'self'" }
routes = [{ to = ":8080" }]
[services.app]
build = "."
user = "image"
[services.app.health]
test = ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/healthz"]
"""
RW = """kind = "rw"
auth = "self"
[sites.main]
[[sites.main.routes]]
paths = ["/api/*"]
to = "api:9000"
max_body = "10MB"
[[sites.main.routes]]
to = "web:8080"
[services.api]
build = "api"
mounts = [{ data = "db", at = "/data" }]
[services.api.health]
test = ["CMD", "true"]
[services.web]
image = "nginxinc/nginx-unprivileged:stable-alpine"
user = "image"
[services.web.health]
test = ["CMD", "true"]
[services.job]
build = "api"
network = "none"
[services.job.health]
test = ["CMD", "true"]
"""


def project(app_tomls, env=ENV):
    """A throwaway server folder holding the given apps ({name: app.toml text})."""
    root = Path(tempfile.mkdtemp())
    (root / "backend/caddy/caddy-public").mkdir(parents=True)
    (root / "backend/caddy/caddy-public/Caddyfile").write_text(CADDYFILE)
    (root / "docker-compose.yml").write_text(BASE)
    (root / ".env").write_text(env)
    for name, text in app_tomls.items():
        d = root / "backend/public-apps" / name
        (d / "api").mkdir(parents=True)
        (d / "Dockerfile").write_text("FROM scratch\n")
        (d / "api/Dockerfile").write_text("FROM scratch\n")
        (d / "app.toml").write_text(text)
        (d / "whitelist.txt").write_text("# nothing\n")
    return root


def use(root):
    apps.ROOT, apps.GEN, apps.APPS = root, root / "generated", root / "backend/public-apps"


def problem(fn):
    try:
        with redirect_stdout(io.StringIO()):
            fn()
    except apps.Problem as e:
        return str(e)
    return None


HAVE_COMPOSE = shutil.which("docker") and subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0

# ---------------------------------------------------------------- a static app and a multi-service rw app
root = project({"site": STATIC, "tool": RW})
use(root)
env = apps.read_env() | {"SITE_DOMAIN": "site.p.example.net", "TOOL_DOMAIN": "tool.p.example.net"}
(root / ".env").write_text(ENV + "SITE_DOMAIN=site.p.example.net\nTOOL_DOMAIN=tool.p.example.net\n")
loaded = apps.load_all(apps.read_env(), {"wg-public", "caddy-public", "jellyfin", "public_net"})
check("both apps load", [a["name"] for a in loaded] == ["site", "tool"])
site, tool = loaded
check("one service: the container is the app", site["services"]["app"]["container"] == "site")
check("several: <app>-<service>", {s["container"] for s in tool["services"].values()} == {"tool-api", "tool-web", "tool-job"})
check("static: encode on, no origin check", site["sites"]["main"]["encode"] and not site["origin"])
check("rw: origin check on by default", tool["origin"])

doc = apps.compose_doc(loaded)
svc = doc["services"]
check("wg-public joins each reachable service's network", set(svc["wg-public"]["networks"]) == {"site_net", "tool-api_net", "tool-web_net"})
check("a service with no network gets network_mode none", svc["tool-job"]["network_mode"] == "none" and "networks" not in svc["tool-job"])
check("networks are internal", all(n == {"internal": True} for n in doc["networks"].values()))
check("hardening on every service", all(s["read_only"] and s["cap_drop"] == ["ALL"] and s["security_opt"] == ["no-new-privileges:true"]
                                        for k, s in svc.items() if k not in ("wg-public", "caddy-public")))
check("defaults: memory, cpus, pids, /tmp", svc["site"]["mem_limit"] == "512m" and svc["site"]["cpus"] == 4
      and svc["site"]["pids_limit"] == 128 and svc["site"]["tmpfs"] == ["/tmp:size=16m,mode=1777"])
check("user: image leaves it to the image, puid sets PUID:PGID", "user" not in svc["site"] and svc["tool-api"]["user"] == "${PUID:-1000}:${PGID:-1000}")
check("logs: static none, rw per service", "volumes" not in svc["site"]
      and "./logs/public-apps/tool/app/api:/logs" in svc["tool-api"]["volumes"])
check("data folder", "./internal-config/public-apps/tool/db:/data" in svc["tool-api"]["volumes"])
check("caddy-public gets the site names and the access-log folders",
      set(svc["caddy-public"]["environment"]) == {"SITE_DOMAIN", "TOOL_DOMAIN"}
      and "./logs/public-apps/tool/access:/logs/public-apps/tool/access" in svc["caddy-public"]["volumes"])
text = "\n".join(apps.yaml(doc))
check("YAML: $ in literal strings is escaped, interpolations aren't",
      apps._scalar("a$b") == '"a$$b"' and apps._scalar(apps.Interp("${X}")) == '"${X}"' and '"${TOOL_DOMAIN:?set TOOL_DOMAIN in .env}"' in text)
check("YAML: lists of maps and empty maps", apps.yaml({"a": [{"b": 1, "c": [2]}], "d": {}}) == ["a:", "  - b: 1", "    c:", "      - 2", "d: {}"])

cad = apps.caddy_doc(tool)
check("multi-route site: a handle per path set, then the rest", "@r1 path /api/*" in cad and "handle @r1 {" in cad
      and "reverse_proxy tool-api:9000" in cad and "max_size 10MB" in cad and "reverse_proxy tool-web:8080" in cad)
check("origin check inside every handle", cad.count("respond @foreign_write") == 2 and cad.count("respond @foreign_ws") == 2)
check("the base headers on every site", all(h in cad for h in ("-Server", 'X-Content-Type-Options "nosniff"', 'Referrer-Policy "no-referrer"')))
check("access log per site, kept for good", "/logs/public-apps/tool/access/access.log" in cad and "roll_keep_for 36500d" in cad)
cad = apps.caddy_doc(site)
check("one catch-all route: proxied straight from the site", "handle" not in cad and "reverse_proxy site:8080" in cad and "encode zstd gzip" in cad)
check("header values quoted", '''Content-Security-Policy "default-src 'self'"''' in cad)

with redirect_stdout(io.StringIO()):
    apps.render(loaded, root / "generated" / ".next")
files = sorted(str(p.relative_to(root / "generated" / ".next")) for p in (root / "generated" / ".next").rglob("*") if p.is_file())
check("render writes one compose file and one Caddy file per app", files == ["caddy-public/site.caddy", "caddy-public/tool.caddy", "docker-compose.apps.yml"])
if HAVE_COMPOSE:
    r = subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "-f", "generated/.next/docker-compose.apps.yml", "config", "--format", "json"],
                       cwd=root, capture_output=True, text=True)
    cfg = json.loads(r.stdout) if r.returncode == 0 else {}
    check("docker compose accepts the rendered file", r.returncode == 0)
    check("compose merges the networks into wg-public", set(cfg.get("services", {}).get("wg-public", {}).get("networks", {})) == {"public_net", "site_net", "tool-api_net", "tool-web_net"})
    check("compose merges the site names into caddy-public", {"PUBLIC_DOMAIN", "SITE_DOMAIN", "TOOL_DOMAIN"} <= set(cfg.get("services", {}).get("caddy-public", {}).get("environment", {})))
    check("relative paths resolve from the server folder", any(v["source"] == str(root / "logs/public-apps/tool/app/api")
                                                              for v in cfg.get("services", {}).get("tool-api", {}).get("volumes", [])))
if os.environ.get("CADDY_BIN"):
    check("caddy validates the rendered sites", problem(lambda: apps.caddy_validate(root / "generated" / ".next",
          {"PUBLIC_DOMAIN": "p.example.net", "SITE_DOMAIN": "site.p.example.net", "TOOL_DOMAIN": "tool.p.example.net"})) is None)

# ---------------------------------------------------------------- what load refuses
def refused(toml, *, name="xa", env_extra="XA_DOMAIN=xa.p.example.net\n", whitelist="", files=None):
    r = project({name: toml}, ENV + env_extra)
    use(r)
    (r / "backend/public-apps" / name / "whitelist.txt").write_text(whitelist) if whitelist is not None \
        else (r / "backend/public-apps" / name / "whitelist.txt").unlink()
    for rel, data in (files or {}).items():
        (r / "backend/public-apps" / name / rel).write_text(data)
    return problem(lambda: apps.load_all(apps.read_env(), {"wg-public", "caddy-public", "jellyfin", "public_net"})) or ""


check("an unknown key (a typo)", "unknown key" in refused(STATIC.replace("user =", "usr =")))
check("a kind that doesn't exist", "kind must be" in refused(STATIC.replace('"static"', '"blog"')))
check("a kind not built yet", "isn't built yet" in refused(STATIC.replace('"static"', '"live"')))
check("rw without auth = self (Authelia comes later)", "later stage" in refused(RW.replace('auth = "self"\n', ""), env_extra="XA_DOMAIN=xa.p.example.net\n"))
check("no whitelist.txt", "whitelist.txt is missing" in refused(STATIC, whitelist=None))
check("a whitelist with hosts (egress comes later)", "later stage" in refused(STATIC, whitelist="api.example.com\n"))
check("a static app with a data folder", "store nothing" in refused(STATIC.replace('user = "image"', 'mounts = [{ data = "d", at = "/d" }]')))
check("a mount that climbs out", "relative path" in refused(RW.replace('data = "db"', 'data = "../../etc"')))
check("a mount onto the Docker socket", "absolute path in the container" in refused(RW.replace('at = "/data"', 'at = "/var/run/docker.sock"')))
check("a route to a service without a network", "nothing can reach it" in refused(RW.replace('to = "web:8080"', 'to = "job:8080"')))
check("a networked service no route reaches", "no route reaches it" in refused(RW.replace('network = "none"\n', "")))
check("a catch-all route before others", "must come last" in refused(RW.replace('paths = ["/api/*"]\n', "")))
check("a header value with a Caddy placeholder", "bad value" in refused(STATIC.replace("default-src 'self'", "{http.request.host}")))
check("both build and image", "exactly one of build" in refused(STATIC.replace('build = "."', 'build = "."\nimage = "x"')))
check("a reserved name", "not reserved" in refused(STATIC, name="www"))
check("a container name the base already uses", "already used" in refused(STATIC, name="jellyfin", env_extra="JELLYFIN_DOMAIN=j.p.example.net\n"))
check("the site's .env line missing", "has no XA_DOMAIN" in refused(STATIC, env_extra=""))
check("a site outside PUBLIC_DOMAIN", "one label under" in refused(STATIC, env_extra="XA_DOMAIN=xa.elsewhere.net\n"))
check("a site on the private domain's auth name", "one label under" in refused(STATIC, env_extra="XA_DOMAIN=auth.m.example.org\n"))
r2 = project({"aa": STATIC.replace("[sites.main]", "[sites.main]\napex = true"), "bb": STATIC.replace("[sites.main]", "[sites.main]\napex = true")})
use(r2)
check("only one app may have the apex", "already has the apex" in (problem(lambda: apps.load_all(apps.read_env(), set())) or ""))
r3 = project({"aa": STATIC, "bb": STATIC}, ENV + "AA_DOMAIN=same.p.example.net\nBB_DOMAIN=same.p.example.net\n")
use(r3)
check("two apps on one address", "same address" in (problem(lambda: apps.load_all(apps.read_env(), set())) or ""))

# ---------------------------------------------------------------- the server's own apps, if any
server = Path(os.environ.get("SERVER_TREE", HERE.parent))
found = sorted(p.parent for p in server.glob("backend/public-apps/*/app.toml"))
if found:
    r4 = project({})
    for d in found:
        shutil.copytree(d, r4 / "backend/public-apps" / d.name, ignore=shutil.ignore_patterns("*.glb", "node_modules", "models"))
    use(r4)
    fake = {}                                   # a made-up address for every site the apps name
    for d in found:
        for key, site in (apps.tomllib.loads((d / "app.toml").read_text()).get("sites") or {}).items():
            if not site.get("apex"):
                fake[apps.site_var(d.name, key, site)] = f"{d.name}-{key}.p.example.net"
    (r4 / ".env").write_text(ENV + "".join(f"{k}={v}\n" for k, v in fake.items()))
    err = problem(lambda: apps.render(apps.load_all(apps.read_env(), {"wg-public", "caddy-public", "public_net"}), r4 / "generated" / ".next"))
    check(f"this server's apps load and render ({', '.join(d.name for d in found)})", err is None)
    if err:
        print("   ", err)

# ---------------------------------------------------------------- small pieces
r5 = project({})
use(r5)
(r5 / ".env").write_bytes(b"A=1\r\nB=2")
apps.env_append("C_DOMAIN", "c.p.example.net")
check(".env: appended after a line break, CRLF kept", (r5 / ".env").read_bytes() == b"A=1\r\nB=2\r\nC_DOMAIN=c.p.example.net\r\n")
check("a starter app.toml loads", (lambda: (
    (r5 / "backend/public-apps/new").mkdir(parents=True), (r5 / "backend/public-apps/new/Dockerfile").write_text("FROM scratch\n"),
    (r5 / "backend/public-apps/new/whitelist.txt").write_text(""),
    (r5 / "backend/public-apps/new/app.toml").write_text(apps.starter("static", False, 8080, "/healthz")),
    apps.load(r5 / "backend/public-apps/new"))[-1]["kind"] == "static")())
z = r5 / "bad.zip"
with zipfile.ZipFile(z, "w") as f:
    f.writestr("app/Dockerfile", "FROM scratch\n")
    f.writestr("../escape.txt", "x")
check("a zip with .. is refused", "refusing" in (problem(lambda: apps._unpack(z, r5 / "out")) or ""))
z2 = r5 / "good.zip"
with zipfile.ZipFile(z2, "w") as f:
    f.writestr("myapp/Dockerfile", "FROM scratch\n")
    f.writestr("myapp/src/a.js", "1")
apps._unpack(z2, r5 / "out2")
check("a zip with one top folder is unwrapped", (r5 / "out2/Dockerfile").exists() and (r5 / "out2/src/a.js").exists())

print(f"{PASSED}/{PASSED + len(FAILED)} checks passed")
sys.exit(1 if FAILED else 0)
