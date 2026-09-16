import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  ErrorState,
  PlannedBadge,
  Spinner,
} from "../components/ui";
import { PlannedModule } from "../components/PlannedModule";
import { ApiClientError } from "../api/client";

describe("ui primitives", () => {
  it("Badge renders tone classes and label", () => {
    const html = renderToStaticMarkup(<Badge tone="green">Active</Badge>);
    expect(html).toContain("bg-green-100");
    expect(html).toContain("Active");
  });

  it("Button with planned renders a visible Planned label and is disabled", () => {
    const html = renderToStaticMarkup(
      <Button planned plannedReason="Not yet.">Do thing</Button>,
    );
    expect(html).toContain("Planned");
    expect(html).toContain("disabled");
    expect(html).toContain('title="Not yet."');
  });

  it("Button without planned has no Planned label", () => {
    const html = renderToStaticMarkup(<Button variant="primary">Save</Button>);
    expect(html).not.toContain("Planned");
    expect(html).not.toContain("disabled=");
  });

  it("EmptyState renders title and hint", () => {
    const html = renderToStaticMarkup(<EmptyState title="No leads" hint="Add one." />);
    expect(html).toContain("No leads");
    expect(html).toContain("Add one.");
  });

  it("Spinner is a live region", () => {
    const html = renderToStaticMarkup(<Spinner label="Loading tasks…" />);
    expect(html).toContain('role="status"');
    expect(html).toContain("Loading tasks…");
  });

  it("ErrorState distinguishes not-implemented from real errors", () => {
    const missing = new ApiClientError(404, { code: "not_found", message: "nope" });
    const html404 = renderToStaticMarkup(<ErrorState error={missing} context="GET /x" />);
    expect(html404).toContain("Not available yet");
    expect(html404).toContain("backend has not implemented it yet");

    const denied = new ApiClientError(403, { code: "policy_denied", message: "Denied by policy" });
    const html403 = renderToStaticMarkup(<ErrorState error={denied} />);
    expect(html403).toContain("Something went wrong");
    expect(html403).toContain("Denied by policy");
    expect(html403).toContain("policy_denied");
  });

  it("DataTable renders headers and rows", () => {
    const html = renderToStaticMarkup(
      <DataTable
        columns={[
          { key: "name", header: "Name", render: (r: { id: string; name: string }) => r.name },
        ]}
        rows={[
          { id: "1", name: "Alpha" },
          { id: "2", name: "Beta" },
        ]}
        caption="test table"
      />,
    );
    expect(html).toContain("<th");
    expect(html).toContain("Name");
    expect(html).toContain("Alpha");
    expect(html).toContain("Beta");
  });

  it("PlannedBadge is present and honest", () => {
    const html = renderToStaticMarkup(<PlannedBadge reason="No contract yet." />);
    expect(html).toContain("Planned");
    expect(html).toContain("No contract yet.");
  });
});

describe("PlannedModule", () => {
  it("renders contract note and planned surface without fake controls", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <PlannedModule
          title="Support"
          subtitle="Tickets."
          contractNote="No support endpoints in API.md yet."
          plannedSurface={[{ name: "Ticket inbox", detail: "Filter tickets." }]}
        />
      </MemoryRouter>,
    );
    expect(html).toContain("Support");
    expect(html).toContain("No support endpoints in API.md yet.");
    expect(html).toContain("Ticket inbox");
    expect(html).toContain("Planned");
    // No working buttons: the only interactive elements are links.
    expect(html).not.toContain("<button");
  });
});
