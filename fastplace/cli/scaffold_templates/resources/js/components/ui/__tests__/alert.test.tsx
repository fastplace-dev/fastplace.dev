import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Alert, AlertDescription, AlertTitle } from "../alert";

afterEach(cleanup);

describe("Alert", () => {
  it("renders the container with the alert role and data-slot", () => {
    render(<Alert>Something happened</Alert>);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveAttribute("data-slot", "alert");
    expect(alert).toHaveTextContent("Something happened");
  });

  it("applies the default variant surface classes", () => {
    render(<Alert>All good</Alert>);
    const alert = screen.getByRole("alert");
    expect(alert.className).toContain("bg-background");
    expect(alert.className).toContain("text-foreground");
  });

  it("maps the destructive variant onto its foreground color", () => {
    render(<Alert variant="destructive">Something went wrong</Alert>);
    expect(screen.getByRole("alert").className).toContain("text-destructive-foreground");
  });

  it("renders AlertTitle text visibly with its slot", () => {
    render(
      <Alert>
        <AlertTitle>Heads up</AlertTitle>
      </Alert>,
    );
    const title = screen.getByText("Heads up");
    expect(title).toHaveAttribute("data-slot", "alert-title");
    expect(title.className).toContain("font-medium");
  });

  it("renders AlertDescription with muted foreground classes", () => {
    render(
      <Alert>
        <AlertDescription>You can undo this action later.</AlertDescription>
      </Alert>,
    );
    const description = screen.getByText("You can undo this action later.");
    expect(description).toHaveAttribute("data-slot", "alert-description");
    expect(description.className).toContain("text-muted-foreground");
  });

  it("lets a caller className override the base padding", () => {
    render(<Alert className="px-6">Padded</Alert>);
    const alert = screen.getByRole("alert");
    expect(alert.className).toContain("px-6");
    expect(alert.className).not.toContain("px-4");
  });
});
