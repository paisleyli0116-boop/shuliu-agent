declare const idBrand: unique symbol;

export type FoundationId<Kind extends string> = string & {
  readonly [idBrand]: Kind;
};

export type UserId = FoundationId<"UserId">;
export type OrganizationId = FoundationId<"OrganizationId">;
export type OrganizationMembershipId = FoundationId<"OrganizationMembershipId">;
export type RoleId = FoundationId<"RoleId">;
export type PermissionId = FoundationId<"PermissionId">;

export interface OrganizationScope {
  organization_id: OrganizationId;
}

export type IdentityRecordStatus = "ACTIVE" | "DISABLED" | "REVOKED";

export interface IdentitySession {
  session_id: string;
  user_id: UserId;
  issued_at: string;
  expires_at: string;
}

export interface IdentityUser {
  user_id: UserId;
  status: IdentityRecordStatus;
}

export interface OrganizationMembership extends OrganizationScope {
  membership_id: OrganizationMembershipId;
  user_id: UserId;
  status: IdentityRecordStatus;
}

export interface Role extends OrganizationScope {
  role_id: RoleId;
  name: string;
  status: IdentityRecordStatus;
}

export interface Permission {
  permission_id: PermissionId;
  permission_code: string;
}
