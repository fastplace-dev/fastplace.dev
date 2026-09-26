import { describe, expect, it } from "vitest";

import { fromBase64Url, toBase64Url } from "../base64url";

describe("base64url", () => {
  it("round-trips arbitrary bytes", () => {
    const bytes = new Uint8Array([0, 1, 2, 3, 250, 251, 252, 253, 254, 255]);
    expect(fromBase64Url(toBase64Url(bytes))).toEqual(bytes);
  });

  it("encodes without padding and URL-unsafe characters", () => {
    // bytes 62/63 (0x3E, 0x3F) map to '+' and '/' in base64, '-' and '_' in base64url.
    expect(toBase64Url(new Uint8Array([254]))).not.toMatch(/[+/=]/);
    expect(fromBase64Url(toBase64Url(new Uint8Array([254])))).toEqual(new Uint8Array([254]));
  });

  it("decodes unpadded input", () => {
    // [1,2,3,4] -> "AQIDBA==" base64 -> "AQIDBA" base64url (padding stripped).
    expect(fromBase64Url("AQIDBA")).toEqual(new Uint8Array([1, 2, 3, 4]));
  });

  it("decodes url-safe variants of + and /", () => {
    expect(fromBase64Url("-_8")).toEqual(fromBase64Url("+/8"));
  });
});
