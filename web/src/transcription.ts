import type { TranscriptionEventKind } from "./api";
import type { InterviewRuntime, InterviewTurnInput } from "./types";
import type { RecordedUtterance } from "./voiceCapture";

const DEFAULT_LIVE_GRACE_MS = 3_000;
const DEFAULT_MAX_PENDING_TURNS = 2;
const MAX_COMPLETED_IDS = 256;

export const COORDINATOR_FATAL_MESSAGE =
  "Candidate transcription is paused. Reconnect to retry this answer.";

export class TranscriptionCoordinatorError extends Error {
  constructor() {
    super(COORDINATOR_FATAL_MESSAGE);
    this.name = "TranscriptionCoordinatorError";
  }
}

export interface TurnTranscriptionOperations {
  saveTurns: (
    interviewId: string,
    items: InterviewTurnInput[],
  ) => Promise<InterviewRuntime>;
  transcribeTurn: (
    interviewId: string,
    clientTurnId: string,
    utterance: RecordedUtterance,
  ) => Promise<InterviewRuntime>;
  acceptLiveTranscript: (
    interviewId: string,
    clientTurnId: string,
  ) => Promise<InterviewRuntime>;
  recordTranscriptionEvent: (
    interviewId: string,
    kind: TranscriptionEventKind,
  ) => Promise<void>;
}

export interface TurnTranscriptionCallbacks {
  onRuntime?: (runtime: InterviewRuntime) => void;
  onFatal?: (error: TranscriptionCoordinatorError) => void;
  onRecovered?: () => void;
}

export interface TurnTranscriptionOptions {
  liveGraceMs?: number;
  maxPendingTurns?: number;
}

type LiveStatus = "waiting" | "saving" | "succeeded" | "failed";
type FinalStatus = "waiting" | "queued" | "saving" | "failed";
type RetainedRecovery = "transcribe" | "accept_live" | "overflow_transcribe";

interface PendingTurn {
  live: LiveStatus;
  final: FinalStatus;
  utterance?: RecordedUtterance;
  liveGraceTimer?: ReturnType<typeof setTimeout>;
  acceptingFallback: boolean;
  liveFailedReported: boolean;
}

interface IdleWaiter {
  resolve: () => void;
  reject: (error: TranscriptionCoordinatorError) => void;
}

interface TurnDrainWaiter {
  resolve: () => void;
  reject: (error: TranscriptionCoordinatorError) => void;
}

interface RetainedTurn {
  utterance: RecordedUtterance;
  recovery: RetainedRecovery;
}

export class TurnTranscriptionCoordinator {
  private readonly liveGraceMs: number;
  private readonly maxPendingTurns: number;
  private readonly turns = new Map<string, PendingTurn>();
  private readonly retained = new Map<string, RetainedTurn>();
  private readonly finalQueue: string[] = [];
  private readonly completed = new Set<string>();
  private readonly idleWaiters = new Set<IdleWaiter>();
  private readonly turnDrainWaiters = new Set<TurnDrainWaiter>();
  private finalInFlight: string | null = null;
  private retryPromise: Promise<void> | null = null;
  private disposed = false;

  constructor(
    private readonly interviewId: string,
    private readonly operations: TurnTranscriptionOperations,
    private readonly callbacks: TurnTranscriptionCallbacks = {},
    options: TurnTranscriptionOptions = {},
  ) {
    this.liveGraceMs = options.liveGraceMs ?? DEFAULT_LIVE_GRACE_MS;
    this.maxPendingTurns = Math.max(
      1,
      options.maxPendingTurns ?? DEFAULT_MAX_PENDING_TURNS,
    );
  }

  liveCompleted(itemId: string, transcript: string): void {
    if (
      this.disposed ||
      this.completed.has(itemId) ||
      this.retained.has(itemId)
    ) {
      return;
    }
    const turn = this.turnFor(itemId);
    if (turn.live !== "waiting") return;

    turn.live = "saving";
    this.report("live_transcription_completed");
    const item: InterviewTurnInput = {
      client_turn_id: itemId,
      speaker: "user",
      transcript,
      delivery_status: "acknowledged",
    };
    void this.operations
      .saveTurns(this.interviewId, [item])
      .then(() => {
        if (!this.owns(itemId, turn) || turn.live !== "saving") return;
        turn.live = "succeeded";
        this.reconcile(itemId, turn);
      })
      .catch(() => {
        this.reportLiveFailure(turn);
        if (!this.owns(itemId, turn) || turn.live !== "saving") return;
        turn.live = "failed";
        this.reconcile(itemId, turn);
      });
  }

  liveFailed(itemId: string): void {
    if (
      this.disposed ||
      this.completed.has(itemId) ||
      this.retained.has(itemId)
    ) {
      return;
    }
    const turn = this.turnFor(itemId);
    if (turn.live !== "waiting") return;
    turn.live = "failed";
    this.reportLiveFailure(turn);
    this.reconcile(itemId, turn);
  }

  audioReady(utterance: RecordedUtterance): void {
    const itemId = utterance.itemId;
    if (
      this.disposed ||
      this.completed.has(itemId) ||
      this.retained.has(itemId)
    ) {
      return;
    }
    const turn = this.turnFor(itemId);
    if (turn.final !== "waiting" || turn.utterance) return;

    if (this.activeAudioCount() >= this.maxPendingTurns) {
      this.removeTurn(itemId, turn);
      this.enterRetainedFailure(
        itemId,
        utterance,
        "overflow_transcribe",
        false,
      );
      return;
    }

    turn.utterance = utterance;
    turn.final = "queued";
    this.finalQueue.push(itemId);
    this.pumpFinalQueue();
  }

  awaitIdle(): Promise<void> {
    if (this.disposed || this.retained.size > 0) {
      return Promise.reject(this.safeError());
    }
    if (this.turns.size === 0) return Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      this.idleWaiters.add({ resolve, reject });
    });
  }

  retryRetained(): Promise<void> {
    if (this.disposed) return Promise.reject(this.safeError());
    if (this.retryPromise) return this.retryPromise;
    if (this.retained.size === 0) return Promise.resolve();

    const retry = async () => {
      const blocking = Array.from(this.retained.entries()).filter(
        ([, turn]) => turn.recovery !== "overflow_transcribe",
      );
      for (const retainedTurn of blocking) {
        await this.retryRetainedTurn(retainedTurn);
      }
      if (this.hasBlockingRetained()) throw this.safeError();

      this.pumpFinalQueue();
      if (this.turns.size > 0) await this.waitForTurnDrain();
      if (this.hasBlockingRetained()) throw this.safeError();

      const overflow = Array.from(this.retained.entries()).filter(
        ([, turn]) => turn.recovery === "overflow_transcribe",
      );
      for (const retainedTurn of overflow) {
        await this.retryRetainedTurn(retainedTurn);
      }
      if (this.retained.size > 0) throw this.safeError();
      this.callbacks.onRecovered?.();
      this.settleIdleWaiters();
    };
    this.retryPromise = retry().finally(() => {
      this.retryPromise = null;
    });
    return this.retryPromise;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const turn of this.turns.values()) {
      this.clearGraceTimer(turn);
      turn.utterance = undefined;
    }
    this.turns.clear();
    this.retained.clear();
    this.finalQueue.length = 0;
    this.finalInFlight = null;
    this.completed.clear();
    this.rejectIdleWaiters();
    this.rejectTurnDrainWaiters();
  }

  private turnFor(itemId: string): PendingTurn {
    let turn = this.turns.get(itemId);
    if (!turn) {
      turn = {
        live: "waiting",
        final: "waiting",
        acceptingFallback: false,
        liveFailedReported: false,
      };
      this.turns.set(itemId, turn);
    }
    return turn;
  }

  private reconcile(itemId: string, turn: PendingTurn): void {
    if (!this.owns(itemId, turn) || turn.final !== "failed") return;
    if (turn.live === "succeeded") {
      this.clearGraceTimer(turn);
      if (turn.acceptingFallback) return;
      turn.acceptingFallback = true;
      void this.operations
        .acceptLiveTranscript(this.interviewId, itemId)
        .then((runtime) => {
          if (!this.owns(itemId, turn)) return;
          this.finishTurn(itemId, turn, runtime);
        })
        .catch(() => {
          if (!this.owns(itemId, turn)) return;
          this.failFallbackAcceptance(itemId, turn);
        });
      return;
    }
    if (turn.live === "failed") {
      this.failTurn(itemId, turn);
      return;
    }
    if (!turn.liveGraceTimer) {
      turn.liveGraceTimer = setTimeout(() => {
        if (!this.owns(itemId, turn) || turn.final !== "failed") return;
        if (turn.live === "waiting" || turn.live === "saving") {
          turn.live = "failed";
          this.reportLiveFailure(turn);
          this.failTurn(itemId, turn);
        }
      }, this.liveGraceMs);
    }
  }

  private failTurn(itemId: string, turn: PendingTurn): void {
    const utterance = turn.utterance;
    this.removeTurn(itemId, turn);
    if (utterance) {
      this.enterRetainedFailure(itemId, utterance, "transcribe", true);
    }
  }

  private failFallbackAcceptance(itemId: string, turn: PendingTurn): void {
    const utterance = turn.utterance;
    this.removeTurn(itemId, turn);
    if (utterance) {
      this.enterRetainedFailure(itemId, utterance, "accept_live", false);
    }
  }

  private enterRetainedFailure(
    itemId: string,
    utterance: RecordedUtterance,
    recovery: RetainedRecovery,
    reportDoubleFailure: boolean,
  ): void {
    const wasHealthy = this.retained.size === 0;
    this.retained.set(itemId, { utterance, recovery });
    if (reportDoubleFailure) this.report("double_transcription_failure");
    this.rejectIdleWaiters();
    if (recovery !== "overflow_transcribe") this.rejectTurnDrainWaiters();
    if (wasHealthy) this.callbacks.onFatal?.(this.safeError());
  }

  private finishTurn(
    itemId: string,
    turn: PendingTurn,
    runtime: InterviewRuntime,
  ): void {
    this.removeTurn(itemId, turn);
    this.rememberCompleted(itemId);
    if (!this.disposed) this.callbacks.onRuntime?.(runtime);
    this.pumpFinalQueue();
    this.settleTurnDrainWaiters();
    this.settleIdleWaiters();
  }

  private removeTurn(itemId: string, turn: PendingTurn): void {
    this.clearGraceTimer(turn);
    turn.utterance = undefined;
    const queuedIndex = this.finalQueue.indexOf(itemId);
    if (queuedIndex >= 0) this.finalQueue.splice(queuedIndex, 1);
    if (this.owns(itemId, turn)) this.turns.delete(itemId);
  }

  private pumpFinalQueue(): void {
    if (
      this.disposed ||
      this.hasBlockingRetained() ||
      this.finalInFlight !== null
    ) {
      return;
    }
    while (this.finalQueue.length > 0) {
      const itemId = this.finalQueue.shift();
      if (!itemId) return;
      const turn = this.turns.get(itemId);
      const utterance = turn?.utterance;
      if (!turn || turn.final !== "queued" || !utterance) continue;

      this.finalInFlight = itemId;
      turn.final = "saving";
      void this.operations
        .transcribeTurn(this.interviewId, itemId, utterance)
        .then((runtime) => {
          if (this.finalInFlight === itemId) this.finalInFlight = null;
          if (!this.owns(itemId, turn) || turn.final !== "saving") {
            this.pumpFinalQueue();
            return;
          }
          this.finishTurn(itemId, turn, runtime);
        })
        .catch(() => {
          if (this.finalInFlight === itemId) this.finalInFlight = null;
          if (!this.owns(itemId, turn) || turn.final !== "saving") {
            this.pumpFinalQueue();
            return;
          }
          turn.final = "failed";
          this.reconcile(itemId, turn);
        });
      return;
    }
  }

  private reportLiveFailure(turn: PendingTurn): void {
    if (turn.liveFailedReported) return;
    turn.liveFailedReported = true;
    this.report("live_transcription_failed");
  }

  private report(kind: TranscriptionEventKind): void {
    void this.operations
      .recordTranscriptionEvent(this.interviewId, kind)
      .catch(() => undefined);
  }

  private activeAudioCount(): number {
    let count = 0;
    for (const turn of this.turns.values()) {
      if (turn.utterance) count += 1;
    }
    return count;
  }

  private hasBlockingRetained(): boolean {
    for (const turn of this.retained.values()) {
      if (turn.recovery !== "overflow_transcribe") return true;
    }
    return false;
  }

  private async retryRetainedTurn([itemId, retainedTurn]: [
    string,
    RetainedTurn,
  ]): Promise<void> {
    if (!this.retained.has(itemId)) return;
    try {
      const runtime =
        retainedTurn.recovery === "accept_live"
          ? await this.operations.acceptLiveTranscript(this.interviewId, itemId)
          : await this.operations.transcribeTurn(
              this.interviewId,
              itemId,
              retainedTurn.utterance,
            );
      if (this.disposed) throw this.safeError();
      this.retained.delete(itemId);
      this.rememberCompleted(itemId);
      this.callbacks.onRuntime?.(runtime);
    } catch {
      throw this.safeError();
    }
  }

  private waitForTurnDrain(): Promise<void> {
    if (this.turns.size === 0) return Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      this.turnDrainWaiters.add({ resolve, reject });
    });
  }

  private owns(itemId: string, turn: PendingTurn): boolean {
    return !this.disposed && this.turns.get(itemId) === turn;
  }

  private clearGraceTimer(turn: PendingTurn): void {
    if (turn.liveGraceTimer) clearTimeout(turn.liveGraceTimer);
    turn.liveGraceTimer = undefined;
  }

  private rememberCompleted(itemId: string): void {
    this.completed.add(itemId);
    if (this.completed.size <= MAX_COMPLETED_IDS) return;
    const oldest = this.completed.values().next().value as string | undefined;
    if (oldest) this.completed.delete(oldest);
  }

  private settleIdleWaiters(): void {
    if (this.disposed || this.retained.size > 0 || this.turns.size > 0) return;
    for (const waiter of this.idleWaiters) waiter.resolve();
    this.idleWaiters.clear();
  }

  private rejectIdleWaiters(): void {
    for (const waiter of this.idleWaiters) waiter.reject(this.safeError());
    this.idleWaiters.clear();
  }

  private settleTurnDrainWaiters(): void {
    if (this.disposed || this.turns.size > 0) return;
    for (const waiter of this.turnDrainWaiters) waiter.resolve();
    this.turnDrainWaiters.clear();
  }

  private rejectTurnDrainWaiters(): void {
    for (const waiter of this.turnDrainWaiters) waiter.reject(this.safeError());
    this.turnDrainWaiters.clear();
  }

  private safeError(): TranscriptionCoordinatorError {
    return new TranscriptionCoordinatorError();
  }
}
