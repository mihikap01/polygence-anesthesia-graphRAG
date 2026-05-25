// Process-wide cache for the search index, settable from both server (fs read)
// and browser (HTTP fetch). Lets `retrieve.ts` stay environment-agnostic.

import type { SearchHit } from "./types";

let _idx: SearchHit[] | null = null;

export function setSearchIndex(idx: SearchHit[]): void {
  _idx = idx;
}

export function getSearchIndex(): SearchHit[] {
  if (!_idx) {
    throw new Error(
      "search index not loaded — server should call loader.getSearchIndex() first; " +
        "browser should fetch /data/search_index.json and call setSearchIndex()"
    );
  }
  return _idx;
}

export function hasSearchIndex(): boolean {
  return _idx !== null;
}
