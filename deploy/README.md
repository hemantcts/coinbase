# Deploying to the VPS

Every push to `main` runs the tests and, if they pass, copies the code to `/home/loewenmut-coinbase/htdocs/coinbase.loewenmut.ch` on the server over SSH
and runs `deploy/server-setup.sh` there. Nothing on the server needs GitHub access, and the
server's `.env` and `data/` directory are never overwritten.

## One-time setup

### 1. A deploy key pair (on your own computer)

```
ssh-keygen -t ed25519 -C "github-deploy" -f coinbase-deploy -N ""
```

This creates `coinbase-deploy` (private, goes to GitHub) and `coinbase-deploy.pub` (public, goes to the server).

### 2. The server

Log in to the Lightsail instance as the sudo user you normally use (on Lightsail that is usually `ubuntu`)
and allow the key:

```
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<contents of coinbase-deploy.pub>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
sudo -n true && echo "passwordless sudo OK"
```

The last line must print `passwordless sudo OK`; the deploy runs Docker through `sudo`.
In the Lightsail console, the instance firewall must allow SSH (port 22) from any address,
because GitHub's runners come from changing IPs. If you have restricted it, add a rule.

### 3. Repository secrets

GitHub → repository → Settings → Secrets and variables → Actions → New repository secret:

| name | value |
|---|---|
| `VPS_HOST` | the server's public IP or `coinbase.loewenmut.ch` |
| `VPS_USER` | the sudo user, e.g. `ubuntu` |
| `VPS_PORT` | `22` unless you changed it |
| `VPS_SSH_KEY` | the full contents of the private file `coinbase-deploy`, including the BEGIN/END lines |

Optionally create an environment named `production` (Settings → Environments) and require a
reviewer on it; then every deploy waits for a click.

### 4. First deploy

Actions → "Deploy to VPS" → Run workflow. Watch the log. On success the last lines show the
containers running and the first trader log lines. From then on every push to `main` deploys.

## The dashboard on https://coinbase.loewenmut.ch

The dashboard listens only on `127.0.0.1:8090` on the server (host port `DASHBOARD_BIND_PORT`, default 8090). The site already exists in
CloudPanel, so point its nginx at the dashboard instead of at files:

1. CloudPanel → Sites → `coinbase.loewenmut.ch` → **Vhost**. Replace the `location / { ... }`
   block (keep everything else, in particular the SSL lines CloudPanel manages) with:

   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8090;
       proxy_http_version 1.1;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_read_timeout 60s;
   }
   ```

   Save; CloudPanel reloads nginx.
2. Site → SSL/TLS → Actions → New Let's Encrypt Certificate (if not already issued).
3. Site → Security → Basic Auth, if your CloudPanel version has it: add a user. This keeps the
   page itself private. The data and the STOP / FLATTEN buttons are additionally protected by
   `DASHBOARD_TOKEN`, found with
   `sudo grep DASHBOARD_TOKEN /home/loewenmut-coinbase/htdocs/coinbase.loewenmut.ch/.env`.

Alternatively delete the site and recreate it as type **Reverse Proxy** with URL `http://127.0.0.1:8090`.

## Day to day, on the server

```
cd /home/loewenmut-coinbase/htdocs/coinbase.loewenmut.ch
sudo docker compose logs -f trader
sudo docker compose exec trader python -m trader status
sudo docker compose exec trader python -m trader control stop      # no new buys
sudo docker compose exec trader python -m trader control resume
sudo docker compose exec trader python -m trader gate --days 730 --granularity 21600
```

The deploy refuses to run while `.env` has `LIVE_TRADING=true`; switching to real money is a
deliberate manual step on the server, never an automated one.
