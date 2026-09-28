import { Form, Head } from "@fastplace/react";
import { REGEXP_ONLY_DIGITS } from "input-otp";
import { useMemo, useState } from "react";

import InputError from "@/components/input-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { OTP_MAX_LENGTH } from "@/hooks/use-two-factor-auth";
import AuthLayout from "@/layouts/auth-layout";

// The challenge is verified by a bridge POST; the mode swap only changes
// which field the form collects (authenticator code vs recovery code).
const TWO_FACTOR_CHALLENGE_URL = "/two-factor-challenge";

export default function TwoFactorChallenge() {
  const [showRecoveryInput, setShowRecoveryInput] = useState<boolean>(false);
  const [code, setCode] = useState<string>("");

  const authConfigContent = useMemo<{
    title: string;
    description: string;
    toggleText: string;
  }>(() => {
    if (showRecoveryInput) {
      return {
        title: "Recovery code",
        description:
          "Please confirm access to your account by entering one of your emergency recovery codes.",
        toggleText: "login using an authentication code",
      };
    }

    return {
      title: "Authentication code",
      description: "Enter the authentication code provided by your authenticator application.",
      toggleText: "login using a recovery code",
    };
  }, [showRecoveryInput]);

  const toggleRecoveryMode = (clearErrors: () => void): void => {
    setShowRecoveryInput(!showRecoveryInput);
    clearErrors();
    setCode("");
  };

  return (
    <AuthLayout title={authConfigContent.title} description={authConfigContent.description}>
      <Head title="Two-factor authentication" />

      <div className="space-y-6">
        <Form
          action={TWO_FACTOR_CHALLENGE_URL}
          method="post"
          className="space-y-4"
          resetOnSuccess={showRecoveryInput ? undefined : ["code"]}
        >
          {({ errors, processing, clearErrors }) => (
            <>
              {showRecoveryInput ? (
                <>
                  <Input
                    name="recovery_code"
                    type="text"
                    placeholder="Enter recovery code"
                    autoFocus={showRecoveryInput}
                    required
                    aria-describedby={errors.recovery_code ? "recovery_code-error" : undefined}
                    aria-invalid={errors.recovery_code ? true : undefined}
                  />
                  <InputError id="recovery_code-error" message={errors.recovery_code?.[0]} />
                </>
              ) : (
                <div className="flex flex-col items-center justify-center space-y-3 text-center">
                  <div className="flex w-full items-center justify-center">
                    <InputOTP
                      name="code"
                      maxLength={OTP_MAX_LENGTH}
                      value={code}
                      onChange={(value) => setCode(value)}
                      disabled={processing}
                      pattern={REGEXP_ONLY_DIGITS}
                      autoFocus
                    >
                      <InputOTPGroup>
                        {Array.from({ length: OTP_MAX_LENGTH }, (_, index) => (
                          <InputOTPSlot key={index} index={index} />
                        ))}
                      </InputOTPGroup>
                    </InputOTP>
                  </div>
                  {/* The OTP widget owns its internal inputs; the error stays
                      visible and announced rather than wired via id. */}
                  <InputError id="code-error" message={errors.code?.[0]} />
                </div>
              )}

              <Button type="submit" className="w-full" disabled={processing}>
                Continue
              </Button>

              <div className="text-muted-foreground text-center text-sm">
                <span>or you can </span>
                <button
                  type="button"
                  className="text-ink cursor-pointer underline decoration-line underline-offset-4 transition-colors duration-300 ease-out hover:decoration-current!"
                  onClick={() => toggleRecoveryMode(clearErrors)}
                >
                  {authConfigContent.toggleText}
                </button>
              </div>
            </>
          )}
        </Form>
      </div>
    </AuthLayout>
  );
}
