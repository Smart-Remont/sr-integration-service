// One Composer run through the Cursor SDK; called by composer.py.
// stdin:  {"model": "...", "prompt": "...", "images": ["<base64 jpeg>", ...]}
// stdout: {"status", "result", "usage", "durationMs", "error"}
// The agent gets no tools: it can only look at the images and answer with text.
import { Agent } from "@cursor/sdk";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const task = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const workDir = mkdtempSync(join(tmpdir(), "kl-composer-"));
let agent;
try {
  agent = await Agent.create({
    apiKey: process.env.CURSOR_API_KEY,
    model: { id: task.model },
    local: { cwd: workDir },
    tools: [],
  });
  const run = await agent.send({
    text: task.prompt,
    images: task.images.map((data) => ({ data, mimeType: "image/jpeg" })),
  });
  const res = await run.wait();
  process.stdout.write(
    JSON.stringify({
      status: res.status,
      result: res.result ?? "",
      usage: res.usage ?? {},
      durationMs: res.durationMs ?? null,
      error: res.error?.message ?? null,
    }),
  );
} catch (error) {
  process.stdout.write(JSON.stringify({ status: "error", result: "", usage: {}, error: String(error?.message ?? error) }));
  process.exitCode = 1;
} finally {
  agent?.close();
  rmSync(workDir, { recursive: true, force: true });
}
