import { NextResponse } from "next/server";
import { getSearchIndex } from "@/lib/graph/loader";

export async function GET() {
  return NextResponse.json(getSearchIndex());
}
