# App generator

Adds public web apps to [my home server](https://github.com/sumanthkolli03/home-server) from a
manifest. An app is a folder with a Dockerfile (or a stock image), an `app.toml` and a
`whitelist.txt`. The generator turns it into the Docker Compose services, internal networks and
Caddy sites that run it, applies them, and waits until every container is healthy. If one isn't,
it puts the previous setup back.

```
bash generator/apps.sh add my-app.zip --name my-app --kind static --prefix hello   # hello.<public domain>
bash generator/apps.sh sync      # after editing an app.toml: render, validate, show the diff, apply
bash generator/apps.sh check     # render and validate only
bash generator/apps.sh list
bash generator/apps.sh remove my-app
```

Python 3.11+, standard library only. It sits in the server's folder as `generator/` and writes
only to `generated/`, which the server's compose file and Caddy pick up.
Support for external libraries coming soon!  
Example repos also coming soon - Contact me if you want access/to upload something !!  

```
| Kind | |
|---|---|
| `static` | done |
| `rw` with `auth = "self"` | done |
| `live` (websockets, its own state) | next |
| `rw` behind the server's Authelia sign-in | next |
| `internal` (loopback only, the owner's own tools) | next |
| egress: an app's own WireGuard tunnel out, allowing only the hosts in `whitelist.txt` | later |
| `discord` bots | later |
```
