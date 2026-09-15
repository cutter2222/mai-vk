// Генерирует TypeScript-типы из contracts/schemas в lib/api/types.ts.
// Запуск: pnpm gen-types (из frontend/) или make gen-contracts (из корня).
import { compile } from "json-schema-to-typescript";
import { readdir, readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemasDir = path.resolve(here, "../../contracts/schemas");
const outFile = path.resolve(here, "../lib/api/types.ts");

const files = (await readdir(schemasDir)).filter((f) => f.endsWith(".schema.json")).sort();
const schemas = new Map();
for (const f of files) {
  const schema = JSON.parse(await readFile(path.join(schemasDir, f), "utf8"));
  schemas.set(f, schema);
}

// Резолвер ссылок вида "common.schema.json#/$defs/id" на локальные файлы.
const resolver = {
  order: 1,
  canRead: true,
  async read(file) {
    const name = path.basename(file.url);
    const schema = schemas.get(name);
    if (!schema) throw new Error(`Схема не найдена: ${file.url}`);
    return JSON.stringify(schema);
  },
};

let out = "/* Сгенерировано scripts/gen-types.mjs из contracts/schemas. Не редактировать вручную. */\n\n";
const seen = new Set();
for (const f of files) {
  if (f === "common.schema.json") continue;
  const schema = schemas.get(f);
  const ts = await compile(schema, schema.title ?? f, {
    cwd: schemasDir,
    bannerComment: "",
    additionalProperties: false,
    declareExternallyReferenced: true,
    $refOptions: { resolve: { file: false, http: false, local: resolver } },
  });
  // Общие определения из common попадают в каждый файл; оставляем первое вхождение.
  for (const block of ts.split(/\n(?=export )/)) {
    const m = block.match(/^export (?:interface|type) (\w+)/);
    const key = m ? m[1] : block;
    if (seen.has(key)) continue;
    seen.add(key);
    out += block.trimEnd() + "\n\n";
  }
}
await mkdir(path.dirname(outFile), { recursive: true });
await writeFile(outFile, out);
console.log(`types.ts: ${seen.size} деклараций из ${files.length - 1} схем`);
