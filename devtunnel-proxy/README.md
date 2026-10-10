# Dev Tunnel proxy image

This image makes an Azure Container App forward every inbound request to a
public Microsoft Dev Tunnel URL. It is intended for short-lived development:
your local FastAPI service can change and restart without rebuilding the bot
image or editing its Container App configuration again.

## Start the local bot and its separate tunnel

Keep the existing MCP tunnel on port 8000 unchanged. In a second terminal,
start the Teams bot locally on port 3978, then host a separate tunnel:

```bash
devtunnel host -p 3978 --protocol http --allow-anonymous
```

Copy the resulting `https://…-3978.…devtunnels.ms` URL. The proxy receives it
through the `DEV_TUNNEL_URL` environment variable; use the URL without a
trailing slash.

For the development Container App configured from this repository, the tunnel
has already been created as `attendance-teams-bot-local.eun1`. After starting
the local service, reconnect it with:

```bash
devtunnel host attendance-teams-bot-local.eun1
```

## Local check

```bash
docker build -t devtunnel-proxy:local devtunnel-proxy
docker run --rm -p 8080:8080 \
  -e DEV_TUNNEL_URL=https://your-tunnel-3978.example.devtunnels.ms \
  devtunnel-proxy:local
```

The image does not contain credentials or application code. Its only runtime
setting is `DEV_TUNNEL_URL`, which must be a Dev Tunnel HTTP(S) origin.
