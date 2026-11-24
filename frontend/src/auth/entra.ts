/** Azure Entra ID popup sign-in. msal-browser is loaded lazily so it never weighs on the main bundle. */
export async function entraIdToken(tenantId: string, clientId: string): Promise<string> {
  const { PublicClientApplication } = await import("@azure/msal-browser");
  const app = new PublicClientApplication({
    auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: window.location.origin },
    cache: { cacheLocation: "sessionStorage" },
  });
  await app.initialize();
  const result = await app.loginPopup({ scopes: ["openid", "profile", "email"] });
  if (!result.idToken) throw new Error("Entra ID did not return an id_token");
  return result.idToken;
}
