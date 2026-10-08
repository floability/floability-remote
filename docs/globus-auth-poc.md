# Globus Auth PoC

This is the first milestone for the Globus Compute backend. It verifies that a
developer can authenticate with Globus and make an authenticated request to
the Compute API. It does not deploy or start a Compute endpoint.

## What You Need

- Python 3.9 or newer;
- a browser on the same computer; and
- a Globus identity.

Notre Dame CRC does not need to provide Globus Compute for this step. Globus
Auth is a hosted identity service. An endpoint becomes necessary when we submit
the first remote function.

## 1. Install the PoC Dependency

From the `floability-remote` repository, activate its development environment
and install the optional Globus dependency:

```bash
source .venv/bin/activate
python -m pip install --upgrade --editable '.[globus]'
```

## 2. Authenticate

Run:

```bash
floability-remote-globus-auth
```

On first use, the Globus Compute SDK will display an authorization URL. Open
that URL, authenticate in the browser, approve the requested access, and
return to the terminal as directed.

For the Anvil PoC, select **ACCESS** on the Globus organization screen and use
the ACCESS identity associated with the Anvil account. If a Globus account
already exists under a Notre Dame or another identity, link the ACCESS identity
to that account instead of maintaining separate Globus accounts.

The successful result is similar to:

```text
[globus] Authentication succeeded.
[globus] Compute endpoints visible to this identity: 0
[globus] No access token or credential was printed.
```

Zero endpoints is acceptable at this stage. It means authentication works but
we have not yet created, or been granted access to, a Compute endpoint.

To deliberately repeat the browser authorization flow, run:

```bash
floability-remote-globus-auth --force-login
```

## 3. Record the Result

Record only:

- whether authentication succeeded;
- which identity provider was used, such as `ACCESS`; and
- the endpoint count.

Do not copy authorization codes, access tokens, refresh tokens, passwords, or
the SDK token cache into the repository or an issue.

## Anvil and CRC

The public Anvil documentation confirms that Anvil provides Globus Transfer,
but it does not document a user-facing Globus Compute endpoint. Do not start a
long-running endpoint agent on an Anvil or CRC login node until the applicable
site policy has been checked.

After authentication succeeds, the next milestone is to select a permitted
host, install a single-user PoC endpoint, obtain its endpoint UUID, and submit
`hello_world` from the laptop.

## References

- [Globus Compute quickstart](https://globus-compute.readthedocs.io/en/stable/quickstart.html)
- [Globus Compute client authentication](https://globus-compute.readthedocs.io/en/stable/sdk/client_user_guide.html)
- [Link Globus identities](https://docs.globus.org/guides/tutorials/manage-identities/link-to-existing/)
- [Anvil FAQ](https://docs.rcac.purdue.edu/userguides/anvil/faqs/)
