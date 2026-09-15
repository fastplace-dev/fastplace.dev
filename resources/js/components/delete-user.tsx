import { useForm } from "@fastplace/react";
import { useRef, type FormEvent } from "react";

import Heading from "@/components/heading";
import InputError from "@/components/input-error";
import PasswordInput from "@/components/password-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";

export default function DeleteUser() {
  const passwordInput = useRef<HTMLInputElement>(null);
  const form = useForm<{ password: string }>({ password: "" });

  // Account deletion demands a fresh password confirmation; a failed
  // attempt keeps the dialog open and puts focus back on the field.
  const handleDelete = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void form.delete("/settings/profile", {
      onSuccess: () => form.reset(),
      onError: () => passwordInput.current?.focus(),
    });
  };

  const resetAndClearErrors = () => {
    form.reset();
    form.clearErrors();
  };

  return (
    <div className="space-y-6">
      <Heading
        variant="small"
        title="Delete account"
        description="Delete your account and all of its resources"
      />
      <div className="space-y-4 rounded-lg border border-destructive/20 bg-destructive/10 p-4">
        <div className="text-danger relative space-y-0.5">
          <p className="font-medium">Warning</p>
          <p className="text-sm">Please proceed with caution, this cannot be undone.</p>
        </div>

        <Dialog>
          <DialogTrigger asChild>
            <Button variant="destructive" data-test="delete-user-button">
              Delete account
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogTitle>Are you sure you want to delete your account?</DialogTitle>
            <DialogDescription>
              Once your account is deleted, all of its resources and data will also be permanently
              deleted. Please enter your password to confirm you would like to permanently delete
              your account.
            </DialogDescription>

            <form onSubmit={handleDelete} className="space-y-6">
              <div className="grid gap-2">
                <Label htmlFor="password" className="sr-only">
                  Password
                </Label>

                <PasswordInput
                  id="password"
                  name="password"
                  ref={passwordInput}
                  value={form.data.password}
                  onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
                    form.setData("password", event.target.value)
                  }
                  placeholder="Password"
                  autoComplete="current-password"
                />

                <InputError message={form.errors.password?.[0]} />
              </div>

              <DialogFooter className="gap-2">
                <DialogClose asChild>
                  <Button variant="secondary" onClick={resetAndClearErrors}>
                    Cancel
                  </Button>
                </DialogClose>

                <Button variant="destructive" disabled={form.processing} asChild>
                  <button type="submit" data-test="confirm-delete-user-button">
                    Delete account
                  </button>
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
