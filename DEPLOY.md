# BunkMate Deployment

This app is ready to be deployed as a public website using Docker.

## Recommended host

Use a platform that supports Docker containers, for example:

- Render
- Railway
- Fly.io
- any VPS with Docker

## Important limitation

BunkMate logs in to the real SRM Academia portal using Playwright. That means:

- the hosting platform must allow outbound browser automation
- Chromium must be available in the deployed container
- login reliability still depends on SRM Academia's live session rules

## Deploy on Render

1. Push this project to GitHub.
2. Create a new Render Web Service.
3. Choose the repository.
4. Render will detect `render.yaml`.
5. Deploy.

After deployment, your public site will be available at the Render URL. If you want a custom domain, connect your domain in Render and point it to the service.

## Local Docker run

```bash
docker build -t bunkmate .
docker run -p 8000:8000 -e HOST=0.0.0.0 -e PORT=8000 bunkmate
```

Then open:

`http://127.0.0.1:8000`

## Custom domain

You can use the name `BunkMate` publicly by:

1. buying a domain such as `bunkmate.app` or `bunkmate.in`
2. attaching it to your hosting provider
3. enabling HTTPS

For HTTPS deployments, set:

- `COOKIE_SECURE=1`
