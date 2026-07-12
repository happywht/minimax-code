import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  ConfirmationDialog,
  confirmationBus,
  requestConfirmation,
} from "../src/components/ConfirmationDialog";

function ConfirmationHarness({ onResult }: { onResult: (accepted: boolean) => void }): JSX.Element {
  return (
    <>
      <button
        type="button"
        data-testid="danger-trigger"
        onClick={async () => {
          const accepted = await requestConfirmation({
            title: "Delete provider?",
            description: "This action cannot be undone.",
            confirmLabel: "Delete Provider",
          });
          onResult(accepted);
        }}
      >
        Delete
      </button>
      <ConfirmationDialog />
    </>
  );
}

describe("ConfirmationDialog", () => {
  beforeEach(() => confirmationBus.reset());

  it("defaults focus to Cancel and restores it to the trigger after cancellation", async () => {
    const onResult = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmationHarness onResult={onResult} />);

    const trigger = screen.getByTestId("danger-trigger");
    await user.click(trigger);

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByTestId("confirmation-cancel")).toHaveFocus();
    expect(screen.getByText("This action cannot be undone.")).toBeInTheDocument();

    await user.click(screen.getByTestId("confirmation-cancel"));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("only resolves true after the explicit destructive confirmation", async () => {
    const onResult = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmationHarness onResult={onResult} />);

    await user.click(screen.getByTestId("danger-trigger"));
    expect(onResult).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("confirmation-confirm"));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
  });

  it("cancels on Escape without activating the underlying dialog", async () => {
    const onResult = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmationHarness onResult={onResult} />);

    await user.click(screen.getByTestId("danger-trigger"));
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
