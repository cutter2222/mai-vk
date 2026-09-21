import { Suspense } from "react";

import { OfficeClient } from "./OfficeClient";

export default function OfficePage() {
  return <Suspense fallback={null}><OfficeClient /></Suspense>;
}