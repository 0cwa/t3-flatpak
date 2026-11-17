import { createHash } from "node:crypto";
import { existsSync, realpathSync } from "node:fs";
import { registerHooks } from "node:module";
import { pathToFileURL } from "node:url";

// expo-device-hub 0.22.1 mounts this router for local Android devices.
const middlewareHash = "1756900c8a765634c83f315cffacca59a3b58258ffd7e9e093e038c922933cf5";
const screenshotGate = `        // Capture has side effects, so the method gate runs before ensure() resolves or starts a device.
        if (url.pathname === "/api/screenshot" && req.method !== "POST") {
            return new Response("method not allowed", {
                status: 405
            });
        }`;
const screenshotRoute = `        // Take a still image without starting or waiting for the video stream.
        if (url.pathname === "/api/screenshot") {
            if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

            let serial;
            try {
                serial = await resolveSerial(url.searchParams.get("device"));
                if (stopped || stoppingSerials.has(serial)) throw new Error("Device session is stopping");
            } catch (err) {
                return Response.json({ ok: false, error: errMsg(err) }, { status: 503 });
            }

            const operation = beginOperation(serial, req.signal);
            try {
                const png = await screencapPng(serial, (command, args, options) =>
                    execBuffer(command, args, { ...options, signal: operation.signal }));
                throwIfAborted(operation.signal, "Screenshot was cancelled");
                return await screenshotResponse(png, url);
            } catch (err) {
                return Response.json({ ok: false, error: errMsg(err) }, { status: 400 });
            } finally {
                operation.finish();
            }
        }`;

export function patchDeviceHub(hubEntry) {
  const path = new URL("../../vendor/serve-emu/dist/middleware.js", hubEntry);
  if (!existsSync(path)) return;
  const middleware = pathToFileURL(realpathSync(path)).href;
  registerHooks({
    load(url, context, nextLoad) {
      const result = nextLoad(url, context);
      if (url !== middleware || result.format !== "module") return result;

      const source = Buffer.from(result.source);
      const hash = createHash("sha256").update(source).digest("hex");
      if (hash !== middlewareHash) {
        console.warn("T3 Flatpak: Android screenshot compatibility patch skipped for an unreviewed device hub.");
        return result;
      }
      return {
        ...result,
        source: 'import { execBuffer } from "./exec.js";\n'
          + source.toString().replace(screenshotGate, screenshotRoute),
      };
    },
  });
}

if (process.argv[1]?.endsWith("/expo-device-hub/dist/server/cli.mjs")) {
  patchDeviceHub(pathToFileURL(process.argv[1]));
}
