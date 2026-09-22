import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

// Evaluate the actual minified patch output, not a second implementation of it.
test("precision SDK preserves signed EMU and hundredths of a point", async ({ page }) => {
  test.skip(!process.env.ONLYOFFICE_PRECISION_SDK, "Requires the opt-in patched SDK copy");
  const sdk = await readFile(process.env.ONLYOFFICE_PRECISION_SDK!, "utf8");
  expect(createHash("sha256").update(sdk).digest("hex")).toBe(process.env.ONLYOFFICE_SDK_SHA256);
  const coordinate = sdk.match(/this\.Pza=function\(x,B,N\)\{[^}]+\}/g);
  const font = sdk.match(/t=p\.Ya\(\)\/100;k\.Jb=t;k\.Pj=t;/g);
  const fontWrite = sdk.match(/N=Math\.round\(100\*x\.Jb\),N=Math\.max\(100,N\),r\.Mp\(17,N\)/g);
  const percent = sdk.match(/r\.Mp\(0,Math\.round\(1E5\*N\.tc\)\)/g);
  const spacing = sdk.match(/T=Math\.round\(N\.(?:tc|nh|Zk)\/\.00352777778\)/g);
  expect(coordinate).toHaveLength(1);
  expect(font).toHaveLength(1);
  expect(fontWrite).toHaveLength(1);
  expect(percent).toHaveLength(1);
  expect(spacing).toHaveLength(3);
  const result = await page.evaluate(({ coordinate, font, fontWrite, percent, spacing }) => {
    const values = [-2147483647, -210685, -36001, -1, 0, 1, 36001, 210685, 2147483647];
    const output: number[] = [];
    const writer = { Mp: (_key: number, value: number) => output.push(value) };
    const write = new Function(`${coordinate}; return this.Pza;`).call(writer);
    for (const value of values) write.call(writer, 0, value / 36000, 36000);
    const fonts = [100, 1406, 1438, 2825, 400000];
    const sizes = fonts.map(value => {
      const properties = new Function("p", `var t,k={}; ${font} return k;`)({ Ya: () => value });
      let saved = 0;
      new Function("x", "r", `var N; ${fontWrite};`)(properties, {
        Mp: (_key: number, size: number) => { saved = size; },
      });
      return saved;
    });
    const points = [0, 1, 1406, 1438, 99999];
    const pointOutputs = spacing.map(expression => points.map(value => {
      const millimetres = value * .00352777778;
      return new Function("N", `var T; ${expression}; return T;`)({
        tc: millimetres, nh: millimetres, Zk: millimetres,
      });
    }));
    const percentages = [0, 1, 99999, 100001, 123456];
    const percentOutput = percentages.map(value => {
      let saved = 0;
      new Function("N", "r", `${percent};`)({ tc: value / 1E5 }, {
        Mp: (_key: number, number: number) => { saved = number; },
      });
      return saved;
    });
    return { values, output, fonts, sizes, points, pointOutputs, percentages, percentOutput };
  }, { coordinate: coordinate![0], font: font![0], fontWrite: fontWrite![0],
    percent: percent![0], spacing: spacing! });
  expect(result.output).toEqual(result.values);
  expect(result.sizes).toEqual(result.fonts);
  for (const output of result.pointOutputs) expect(output).toEqual(result.points);
  expect(result.percentOutput).toEqual(result.percentages);
});