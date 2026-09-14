import { describe, expect, it } from "vitest";

import spec from "../../openapi.json";
import type { components } from "./openapi.gen";

// The generated types come from web/openapi.json (npm run gen:api, run by pretest/prebuild). These
// assignments fail `tsc` if the backend drops a field the owner UI depends on.
type TransactionView = components["schemas"]["TransactionView"];
type TxStatus = components["schemas"]["TxStatus"];

const created: TxStatus = "CREATED";
const contractFields: Array<keyof TransactionView> = [
  "status",
  "terminal",
  "status_history",
  "allowed_actions",
  "audit",
];

describe("API contract snapshot", () => {
  it("publishes all 18 transaction statuses as an enum", () => {
    const statuses: readonly string[] = spec.components.schemas.TxStatus.enum;
    expect(statuses).toHaveLength(18);
    expect(statuses).toContain(created);
  });

  it("carries every TransactionView field the owner UI reads", () => {
    expect(Object.keys(spec.components.schemas.TransactionView.properties)).toEqual(
      expect.arrayContaining(contractFields),
    );
  });
});
