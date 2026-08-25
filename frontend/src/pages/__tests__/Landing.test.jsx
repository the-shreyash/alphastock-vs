/**
 * Public landing page.
 *
 * This is the only screen an anonymous visitor sees, and the only one that
 * renders market figures without a session behind it. The tests below cover the
 * two things that are easy to get wrong there and impossible to notice by
 * looking at the page:
 *
 *  1. `/api/market/overview` is public but *fallible* — it answers
 *     `{ available: false }`, with no index payload at all, whenever the market
 *     gateway cannot serve indices. The page keeps a set of representative
 *     sample figures for that case, so the live/sample labelling has to follow
 *     the payload. A page that prints placeholder numbers under a "LIVE" badge
 *     misrepresents the product to someone with no way to tell the difference.
 *
 *  2. The page must survive the request failing outright. It is the front door;
 *     a rejected promise must not blank it.
 */
import { screen, waitFor, within } from "@testing-library/react";
import Landing from "../Landing";
import {
  renderWithProviders,
  installApiMock,
  mockUnauthenticatedUser,
  HTTP,
} from "../../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  mockUnauthenticatedUser(mock);
});

afterEach(() => {
  mock.restore();
});

/** A gateway response carrying real index values. */
const liveOverview = {
  available: true,
  nifty: { value: 24567.8, change_pct: 0.91 },
  sensex: { value: 80912.4, change_pct: 0.77 },
  bank_nifty: { value: 52800.15, change_pct: 1.05 },
};

/** What the gateway actually returns when it cannot serve indices. */
const unavailableOverview = {
  available: false,
  note: "Live market data is temporarily unavailable. Please retry shortly.",
};

function renderLanding() {
  return renderWithProviders(<Landing />);
}

describe("landing page", () => {
  it("renders the hero and routes both calls to action at the auth pages", async () => {
    mock.onGet("/market/overview").reply(HTTP.OK, liveOverview);
    renderLanding();

    expect(await screen.findByTestId("landing-page")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /understand the market/i, level: 1 })
    ).toBeInTheDocument();

    expect(screen.getByTestId("hero-cta-btn")).toHaveAttribute("href", "/register");
    expect(screen.getByTestId("landing-login-btn")).toHaveAttribute("href", "/login");
    expect(screen.getByTestId("landing-signup-btn")).toHaveAttribute("href", "/register");
  });

  it("labels the index card LIVE and shows the gateway's value when data is available", async () => {
    mock.onGet("/market/overview").reply(HTTP.OK, liveOverview);
    renderLanding();

    const badge = await screen.findByTestId("mi-live-badge");
    await waitFor(() => expect(badge).toHaveTextContent("LIVE"));

    // The figure on screen is the one the gateway sent, not the sample.
    const card = screen.getByTestId("mi-index-card");
    expect(within(card).getByText("24,567.8")).toBeInTheDocument();
    expect(within(card).getByText(/\+0\.91% today/)).toBeInTheDocument();
  });

  it("labels the index card SAMPLE when the gateway reports data unavailable", async () => {
    mock.onGet("/market/overview").reply(HTTP.OK, unavailableOverview);
    renderLanding();

    const badge = await screen.findByTestId("mi-live-badge");
    await waitFor(() => expect(badge).toHaveTextContent("SAMPLE"));
    expect(badge).not.toHaveTextContent("LIVE");

    // `available: false` carries no index payload, so the card falls back to
    // its sample figure — which is exactly why it must not claim to be live.
    const card = screen.getByTestId("mi-index-card");
    expect(within(card).getByText("24,320")).toBeInTheDocument();
  });

  it("still renders, labelled SAMPLE, when the market request fails", async () => {
    mock.onGet("/market/overview").reply(HTTP.SERVER_ERROR);
    renderLanding();

    expect(await screen.findByTestId("landing-page")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /understand the market/i, level: 1 })
    ).toBeInTheDocument();

    const badge = await screen.findByTestId("mi-live-badge");
    await waitFor(() => expect(badge).toHaveTextContent("SAMPLE"));
  });

  it("exposes no placeholder links that navigate nowhere", async () => {
    mock.onGet("/market/overview").reply(HTTP.OK, liveOverview);
    renderLanding();
    await screen.findByTestId("landing-page");

    /*
     * `href="#"` scrolls the visitor back to the top while presenting itself as
     * a real destination to assistive tech. Entries without a page yet are
     * plain text instead, so every anchor here has somewhere to go.
     */
    const deadLinks = screen
      .getAllByRole("link")
      .filter((a) => (a.getAttribute("href") ?? "").trim() === "#");
    expect(deadLinks).toEqual([]);
  });
});
