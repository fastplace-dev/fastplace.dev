import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import AlertError from "../alert-error";

afterEach(cleanup);

describe("AlertError", () => {
  it("renders a destructive alert with the fallback title", () => {
    render(<AlertError errors={["First failure"]} />);

    const alert = screen.getByRole("alert");
    expect(alert.className).toContain("text-destructive");
    expect(alert.className).toContain("border-destructive/50");
    expect(alert).toHaveTextContent("Something went wrong.");
  });

  it("renders the caller-provided title instead of the fallback", () => {
    render(<AlertError errors={["Boom"]} title="Could not save your profile" />);

    expect(screen.getByText("Could not save your profile")).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong.")).not.toBeInTheDocument();
  });

  it("lists every error as a list item", () => {
    render(
      <AlertError
        errors={["The name field is required.", "The email has already been taken."]}
        title="Validation failed"
      />,
    );

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("The name field is required.");
    expect(items[1]).toHaveTextContent("The email has already been taken.");
  });

  it("deduplicates repeated errors before rendering", () => {
    render(<AlertError errors={["Same problem", "Same problem", "Other problem"]} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(screen.getAllByText("Same problem")).toHaveLength(1);
  });

  it("renders the alert icon inside the alert", () => {
    render(<AlertError errors={["Boom"]} />);

    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("Something went wrong.")).toBeInTheDocument();
    expect(alert.querySelector("svg")).toBeInTheDocument();
  });

  it("renders the error list with list styling inside the description slot", () => {
    render(<AlertError errors={["Boom"]} />);

    const list = screen.getByRole("list");
    expect(list.className).toContain("list-disc");
    expect(list.className).toContain("list-inside");
  });
});
