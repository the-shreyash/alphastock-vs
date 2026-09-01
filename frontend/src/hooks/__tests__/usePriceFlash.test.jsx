/**
 * D5.18 — the visual contract for a live price update (brief §7, §18).
 *
 * A tick must change the number and must NOT produce a blink: no background
 * flash, no scale/transform (which moves the glyphs and reads as a layout
 * jump), and no colour that toggles merely because a tick arrived.
 *
 * Observed live on 2026-09-01 during NSE hours: with the broker feed stable,
 * the dashboard produced ~24,968 class/style mutations in 100 seconds — about
 * 250/sec — driven by a GSAP tween that wrote `backgroundColor` and `scale`
 * inline on every price change. That is the "blinking" in the brief, and it is
 * also why the index cards rendered a visible highlight box behind the number.
 *
 * These tests pin the contract at the hook, which is the single place every
 * price surface (Dashboard, Portfolio, Watchlist, TradeMonitor) goes through.
 */
import { render } from "@testing-library/react";
import gsap from "gsap";
import { usePriceFlash } from "../usePriceFlash";

// GSAP tweens asynchronously, so asserting only on inline style races its
// ticker: against the pre-D5.18 hook six of these seven tests passed simply
// because the tween had not written yet. Spying on the animation *request*
// makes the contract deterministic — the hook must not ASK for a background
// or transform animation, whether or not the clock has run.
jest.mock("gsap", () => ({
  __esModule: true,
  default: { fromTo: jest.fn(), to: jest.fn(), set: jest.fn(), killTweensOf: jest.fn() },
}));

const animationCalls = () =>
  [...gsap.fromTo.mock.calls, ...gsap.to.mock.calls, ...gsap.set.mock.calls];

const animatedProps = () =>
  animationCalls().flatMap((call) => call.slice(1).flatMap((arg) => Object.keys(arg || {})));

beforeEach(() => {
  gsap.fromTo.mockClear(); gsap.to.mockClear(); gsap.set.mockClear();
});

function Price({ value }) {
  const ref = usePriceFlash(value);
  return <div ref={ref} data-testid="price">{value}</div>;
}

const styleOf = (el) => el.getAttribute("style") || "";

describe("usePriceFlash visual contract", () => {
  test("a rising tick updates the number", () => {
    const { getByTestId, rerender } = render(<Price value={100} />);
    rerender(<Price value={101} />);
    expect(getByTestId("price").textContent).toBe("101");
  });

  test("a rising tick requests no background flash", () => {
    const { rerender } = render(<Price value={100} />);
    rerender(<Price value={101} />);
    expect(animatedProps()).not.toContain("backgroundColor");
  });

  test("a falling tick requests no background flash either", () => {
    const { rerender } = render(<Price value={100} />);
    rerender(<Price value={99} />);
    expect(animatedProps()).not.toContain("backgroundColor");
  });

  test("a tick requests no scale — nothing may move (no layout jump)", () => {
    const { rerender } = render(<Price value={100} />);
    rerender(<Price value={101} />);
    const props = animatedProps();
    expect(props).not.toContain("scale");
    expect(props).not.toContain("transform");
  });

  test("a tick starts no animation at all", () => {
    const { rerender } = render(<Price value={100} />);
    rerender(<Price value={101} />);
    expect(animationCalls()).toHaveLength(0);
  });

  test("rapid consecutive ticks leave no residual inline animation state", () => {
    const { getByTestId, rerender } = render(<Price value={100} />);
    for (const v of [101, 100.5, 102, 101.5, 103, 102.5, 104]) {
      rerender(<Price value={v} />);
    }
    const el = getByTestId("price");
    expect(el.textContent).toBe("104");
    expect(styleOf(el)).not.toMatch(/background|transform|scale/i);
    expect(animationCalls()).toHaveLength(0);
  });

  test("an unchanged value is inert", () => {
    const { getByTestId, rerender } = render(<Price value={100} />);
    rerender(<Price value={100} />);
    expect(styleOf(getByTestId("price"))).not.toMatch(/background|transform|scale/i);
    expect(animationCalls()).toHaveLength(0);
  });

  test("independent instances update independently", () => {
    function Pair({ a, b }) {
      const ra = usePriceFlash(a);
      const rb = usePriceFlash(b);
      return (<>
        <div ref={ra} data-testid="a">{a}</div>
        <div ref={rb} data-testid="b">{b}</div>
      </>);
    }
    const { getByTestId, rerender } = render(<Pair a={10} b={20} />);
    rerender(<Pair a={11} b={20} />);
    expect(getByTestId("a").textContent).toBe("11");
    expect(getByTestId("b").textContent).toBe("20");
    expect(styleOf(getByTestId("a"))).not.toMatch(/background|transform|scale/i);
    expect(animatedProps()).not.toContain("backgroundColor");
  });
});
