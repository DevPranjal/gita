// Execute frozen official code rather than approximate official metric formulas.
import {pathToFileURL} from "node:url";
import {readFileSync} from "node:fs";
import {join} from "node:path";

const root = process.argv[2];
const resolver = await import(pathToFileURL(join(root, "scripts", "lib", "match-types.ts")).href);
const scorer = await import(pathToFileURL(join(root, "scripts", "eval", "scorer.ts")).href);
const input = JSON.parse(readFileSync(0, "utf8"));
const results = input.rows.map(row => {
  const resolved = resolver.resolveForScoring(row.correspondences, row.golden.length);
  if (input.resolve_only) {
    return {matched: [...resolved.matchedToGolden], covered: [...resolved.coveredGolden],
            unmatched: [...resolved.unmatched]};
  }
  const classes = new Map(row.classifications.map(value => [value.candidate_index, value]));
  if (classes.size !== resolved.unmatched.size
      || [...resolved.unmatched].some(index => !classes.has(index))) {
    throw new Error("Missing/extra unmatched classification; refuse official default-FP fallback");
  }
  return scorer.scorePR({
    pr_key: row.pr_key, golden: row.golden, candidate_count: row.candidate_count,
    matched: resolved.matchedToGolden, unmatched: resolved.unmatched,
    covered_golden: resolved.coveredGolden, unmatched_classifications: classes,
  });
});
console.log(JSON.stringify(input.resolve_only ? results : scorer.aggregate(results)));
