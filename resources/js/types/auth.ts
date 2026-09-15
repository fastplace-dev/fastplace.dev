import type { PageProps } from "@fastplace/react";

/** The authenticated user, as delivered by the future Fastplace auth props. */
export type User = {
  id: number;
  name: string;
  email: string;
  avatar?: string;
  email_verified_at: string | null;
  two_factor_enabled?: boolean;
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
};

export type Auth = {
  user: User;
};

export type Passkey = {
  id: number;
  name: string;
  authenticator: string | null;
  created_at_diff: string;
  last_used_at_diff: string | null;
};

export type TwoFactorSetupData = {
  svg: string;
  url: string;
};

export type TwoFactorSecretKey = {
  secretKey: string;
};

/** Props every Fastplace page shares once the bridge attaches auth data. */
export type SharedData = {
  name: string;
  /** Absent until an auth backend exists — read it as `auth?.user`. */
  auth?: Auth;
  sidebarOpen: boolean;
} & PageProps;
