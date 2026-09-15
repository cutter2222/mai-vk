// Генерирует TypeScript-типы из contracts/schemas в lib/api/types.ts.
// Запуск: pnpm gen-types (из frontend/) или make gen-contracts (из корня).
//
// Все схемы собираются в один документ с $defs, как в scripts/gen_contracts.py,
// поэтому общие определения генерируются один раз, а одноимённые вложенные
// объекты разных схем получают разные имена (генератор нумерует дубликаты).
import { compile } from "json-schema-to-typescript";
import { readdir, readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemasDir = path.resolve(here, "../../contracts/schemas");
const outFile = path.resolve(here, "../lib/api/types.ts");

function rewriteRefs(node, selfName) {
  if (Array.isArray(node)) return node.map((v) => rewriteRefs(v, selfName));
  if (node && typeof node === "object") {
    const out = {};
    for (const [key, value] of Object.entries(node)) {
      if (key === "$ref" && typeof value === "string") {
        let ref = value;
        if (ref.startsWith("#/$defs/")) ref = `#/$defs/${selfName}/$defs/${ref.slice("#/$defs/".length)}`;
        else if (ref.startsWith("#/")) ref = `#/$defs/${selfName}/${ref.slice(2)}`;
        else if (ref.includes(".schema.json")) {
          const [file, frag = ""] = ref.split("#");
          const other = file.replace(".schema.json", "");
          if (frag.startsWith("/$defs/")) ref = `#/$defs/${other}/$defs/${frag.slice("/$defs/".length)}`;
          else if (frag) ref = `#/$defs/${other}/${frag.replace(/^\//, "")}`;
          else ref = `#/$defs/${other}`;
        }
        out[key] = ref;
      } else {
        out[key] = rewriteRefs(value, selfName);
      }
    }
    return out;
  }
  return node;
}

const files = (await readdir(schemasDir)).filter((f) => f.endsWith(".schema.json")).sort();
const combined = { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Contracts", type: "object", additionalProperties: false, $defs: {}, properties: {} };
for (const f of files) {
  const name = f.replace(".schema.json", "");
  const schema = JSON.parse(await readFile(path.join(schemasDir, f), "utf8"));
  delete schema.$schema;
  delete schema.$id;
  if (name === "common") schema.title = "Common";
  combined.$defs[name] = rewriteRefs(schema, name);
  if (name !== "common") combined.properties[name] = { $ref: `#/$defs/${name}` };
}

const ts = await compile(combined, "Contracts", {
  cwd: schemasDir,
  bannerComment: "/* Сгенерировано scripts/gen-types.mjs из contracts/schemas. Не редактировать вручную. */",
  additionalProperties: false,
  declareExternallyReferenced: true,
  $refOptions: { resolve: { file: false, http: false } },
});
await mkdir(path.dirname(outFile), { recursive: true });
await writeFile(outFile, ts);
const count = (ts.match(/^export (?:interface|type) /gm) ?? []).length;
console.log(`types.ts: ${count} деклараций из ${files.length - 1} схем`);
