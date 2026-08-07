import { describe, expect, it, vi } from "vitest";

import {
  absoluteSpeechSegment,
  buildTypedAnswerEvents,
  countUnicodeCharacters,
  prepareInputMedia,
  realtimeEventAcknowledgesTurn,
  typedAnswerError,
} from "./realtime";

it("maps Realtime audio offsets to an absolute observable speech segment", () => {
  expect(
    absoluteSpeechSegment(
      1_000,
      2_500,
      new Date("2026-08-07T10:00:00.000Z").getTime(),
    ),
  ).toEqual({
    started_at: "2026-08-07T10:00:01.000Z",
    ended_at: "2026-08-07T10:00:02.500Z",
  });
});

describe("developer text Realtime input", () => {
  it("never requests microphone media in text mode", async () => {
    const getUserMedia = vi.fn();
    const stop = vi.fn();
    const existing = {
      getTracks: () => [{ stop }],
    } as unknown as MediaStream;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    await expect(prepareInputMedia("text_dev", existing)).resolves.toBeNull();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(stop).toHaveBeenCalledOnce();
  });

  it("counts Unicode characters and rejects overflow without truncating", () => {
    const answer = `First paragraph\n\n${"🙂".repeat(19_983)}`;
    expect(countUnicodeCharacters(answer)).toBe(20_000);
    expect(typedAnswerError(answer, 20_000)).toBeNull();

    const oversized = `${answer}界`;
    expect(typedAnswerError(oversized, 20_000)).toMatch(/20,001 characters/);
    expect(oversized.endsWith("界")).toBe(true);
  });

  it("preserves multiline text and uses one stable item ID", () => {
    const text =
      "Reasoning:\n- option A\n- option B\n\n```ts\nconst x = 1;\n```";
    const [itemEvent, responseEvent] = buildTypedAnswerEvents(
      text,
      "item_stable_1",
    );

    expect(itemEvent).toMatchObject({
      type: "conversation.item.create",
      event_id: "item_stable_1",
      item: {
        content: [{ type: "input_text", text }],
      },
    });
    expect(responseEvent).toEqual({ type: "response.create" });
  });

  it("acknowledges only the matching typed conversation item", () => {
    expect(
      realtimeEventAcknowledgesTurn(
        { type: "response.output_audio_transcript.delta", delta: "Thanks" },
        "item_123",
        "My answer",
      ),
    ).toBe(false);
    expect(
      realtimeEventAcknowledgesTurn(
        { type: "conversation.item.added", item: { id: "item_123" } },
        "item_123",
        "My answer",
      ),
    ).toBe(true);
    expect(
      realtimeEventAcknowledgesTurn(
        { type: "conversation.item.added", item: { id: "other" } },
        "item_123",
        "My answer",
      ),
    ).toBe(false);
    expect(
      realtimeEventAcknowledgesTurn(
        {
          type: "conversation.item.created",
          item: {
            id: "server_generated",
            role: "user",
            content: [{ type: "input_text", text: "My answer" }],
          },
        },
        "item_123",
        "My answer",
      ),
    ).toBe(true);
  });
});
