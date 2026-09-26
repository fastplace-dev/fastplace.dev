import "react";

// WebKit's password-rule syntax (used by passkey-capable password fields);
// React's own typings stop short of this attribute, so extend them here.
declare module "react" {
  interface InputHTMLAttributes<_T> {
    passwordrules?: string;
  }
}
