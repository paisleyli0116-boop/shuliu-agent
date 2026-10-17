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
