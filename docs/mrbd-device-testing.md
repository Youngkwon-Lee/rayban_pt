# MRBD Device Testing

This checklist is for testing `server/static/glass-webapp/` on Meta Ray-Ban Display glasses.

## Preferred Test Topology

Use a same-origin HTTPS bridge when possible:

```text
Meta Ray-Ban Display
  -> https://<bridge-host>/glass-app/
  -> same origin /glass/state and /glass/command
  -> local Rayban PT bridge
  -> iOS DAT app
```

This avoids putting bridge credentials or patient context into a public static hosting URL.

If you need a standalone public static preview, the web app supports:

```text
https://stage-rayban-pt-mrbd-hud.vercel.app/?bridge_url=https%3A%2F%2F<bridge-host>&api_key=<short-lived-test-key>
```

Use this only with non-PHI test data and a short-lived bridge API key.

## Local Proof

From the bridge directory:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python smoke_test.py
```

For a browser preview:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8792
open "http://127.0.0.1:8792/glass-app/"
```

Expected:

- viewport is 600 x 600
- no browser console errors
- arrow keys move focus across four commands
- Enter sends the focused command to `/glass/command`
- patient identity appears as an alias, not full PHI

## Standalone HTTPS Preview

The web app directory now includes a Vercel-compatible static wrapper:

- `server/static/glass-webapp/package.json`
- `server/static/glass-webapp/server.js`
- `server/static/glass-webapp/vercel.json`

Check Vercel login:

```bash
vercel whoami
```

Deploy a staging preview:

```bash
cd /Users/youngkwon/projects/rayban_pt/server/static/glass-webapp
URL=$(vercel --yes)
PROJECT_ID=$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['projectId'])")
echo '{"ssoProtection":null}' | vercel api "/v9/projects/$PROJECT_ID" -X PATCH --input - --silent
vercel alias set "$URL" stage-rayban-pt-mrbd-hud.vercel.app
```

Stable preview URL:

```text
https://stage-rayban-pt-mrbd-hud.vercel.app
```

If using a remote bridge:

```text
https://stage-rayban-pt-mrbd-hud.vercel.app/?bridge_url=https%3A%2F%2F<bridge-host>&api_key=<short-lived-test-key>
```

## Meta AI App Deep Link

QR/deep-link format:

```text
fb-viewapp://web_app_deep_link?appName=stage-rayban-pt-mrbd-hud&appUrl=<url-encoded-stage-url>
```

Generate the encoded URL:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python mrbd_device_link.py \
  "https://stage-rayban-pt-mrbd-hud.vercel.app"
```

Then generate a QR code using the installed Meta Wearables QR skill script or any local QR utility.

## On-Device Verification

1. Add the web app through the Meta AI app using the QR code or manual URL.
2. Open the app on the display.
3. Confirm the HUD loads within a few seconds.
4. Use Neural Band / D-pad navigation to move focus:
   - record
   - patient
   - cue
   - history
5. Press Enter on each command and verify the iOS app receives the command through `/glass/command`.
6. Start and stop recording from the HUD.
7. Confirm lens text stays minimal:
   - no full patient identity
   - no raw note text
   - no model output longer than a short cue
8. Confirm errors appear as short “확인 필요” state, with details only in iPhone/bridge logs.

## Stop Conditions

Stop the device test if any of these occur:

- full patient name or note text appears on the lens
- public URL requires a long-lived production API key
- Vercel preview prompts for login on glasses
- command focus cannot be reached with D-pad
- Enter triggers the wrong command
- iOS DAT recording state diverges from `/glass/state`
