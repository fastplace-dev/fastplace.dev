import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { describe, expect, it, afterEach } from "vitest";
import React from "react";
import { FastplaceProvider, router, useCan } from "../index";

const Probe = () => {
  const can = useCan();
  return (
    <ul data-testid="can-map">
      {Object.entries(can).map(([ability, allowed]) => (
        <li key={ability}>
          {ability}={String(allowed)}
        </li>
      ))}
    </ul>
  );
};

function renderWithProps(props: Record<string, unknown>) {
  return render(
    <FastplaceProvider initialPage={{ component: "Probe", props, url: "/probe" }}>
      <Probe />
    </FastplaceProvider>,
  );
}

describe("useCan", () => {
  // The page store is module-global — reset it or the next render keeps
  // the previous test's page (FastplaceProvider only seeds a null store).
  afterEach(() => {
    cleanup();
    router.reset();
  });

  it("reads the ability map from the shared auth props", () => {
    renderWithProps({
      auth: {
        user: { id: 7, email: "member@example.com" },
        can: { "view-dashboard": true, reboot: false },
      },
    });
    expect(screen.getByText("view-dashboard=true")).toBeInTheDocument();
    expect(screen.getByText("reboot=false")).toBeInTheDocument();
  });

  it("returns an empty map when the page carries no auth props", () => {
    const { getByTestId } = renderWithProps({ posts: ["a"] });
    expect(getByTestId("can-map").children.length).toBe(0);
  });

  it("returns an empty map when auth exists without a can map", () => {
    const { getByTestId } = renderWithProps({ auth: { user: null } });
    expect(getByTestId("can-map").children.length).toBe(0);
  });
});
