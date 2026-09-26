import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../card";

afterEach(cleanup);

describe("Card", () => {
  it("renders a full composition with the title exposed as a heading", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle role="heading">Project Overview</CardTitle>
          <CardDescription>A brief summary of the project.</CardDescription>
        </CardHeader>
        <CardContent>Details about the project.</CardContent>
        <CardFooter>Footer actions</CardFooter>
      </Card>,
    );

    expect(screen.getByRole("heading", { name: "Project Overview" })).toBeInTheDocument();
    expect(screen.getByText("A brief summary of the project.")).toBeInTheDocument();
    expect(screen.getByText("Details about the project.")).toBeInTheDocument();
    expect(screen.getByText("Footer actions")).toBeInTheDocument();
  });

  it("renders the card surface with its data-slot and base classes", () => {
    render(<Card>Plain card</Card>);

    const card = screen.getByText("Plain card");
    expect(card).toHaveAttribute("data-slot", "card");
    expect(card.className).toContain("bg-card");
    expect(card.className).toContain("rounded-xl");
  });

  it("assigns every subcomponent its data-slot attribute", () => {
    const { container } = render(
      <Card>
        <CardHeader>
          <CardTitle>title</CardTitle>
          <CardDescription>description</CardDescription>
        </CardHeader>
        <CardContent>content</CardContent>
        <CardFooter>footer</CardFooter>
      </Card>,
    );

    expect(container.querySelector('[data-slot="card-header"]')).toBeInTheDocument();
    expect(screen.getByText("title")).toHaveAttribute("data-slot", "card-title");
    expect(screen.getByText("description")).toHaveAttribute("data-slot", "card-description");
    expect(screen.getByText("content")).toHaveAttribute("data-slot", "card-content");
    expect(screen.getByText("footer")).toHaveAttribute("data-slot", "card-footer");
  });

  it("styles the description with the muted foreground token", () => {
    render(<CardDescription>Secondary text</CardDescription>);

    const description = screen.getByText("Secondary text");
    expect(description).toHaveAttribute("data-slot", "card-description");
    expect(description.className).toContain("text-muted-foreground");
    expect(description.className).toContain("text-sm");
  });

  it("lets a caller className override the base radius", () => {
    render(<Card className="rounded-2xl">Shaped card</Card>);

    const card = screen.getByText("Shaped card");
    expect(card.className).toContain("rounded-2xl");
    expect(card.className).not.toContain("rounded-xl");
  });
});
