# @openclear/embed

Use this package to show OpenClear's positive pay pages inside your online banking, so your business customers don't have to sign in again. It's licensed Apache-2.0 and has no dependencies.

## Setting it up

1. **The bank** lists your online banking's origin under **Settings → Embedding** in OpenClear.

2. **Your server** asks OpenClear for a one-time launch URL. It does this for the person who is signed in, either with token exchange or by acting as that person:

   ```ts
   const person = await oc.forPerson(idToken); // @openclear/sdk
   const { url } = await person.embed.createLaunch({ parent_origin: "https://banking.yourbank.example", path: "/ui/exceptions" });
   ```

3. **Your page** mounts the launch URL:

   ```ts
   import { mount } from "@openclear/embed";

   const openclear = mount(document.getElementById("positive-pay")!, {
     url,
     onEvent: (e) => { if (e.type === "exception-decided") showToast("Decision recorded"); },
     onSessionEnded: async () => openclear.navigate(await getNewLaunchUrl()),
   });
   ```

## How it behaves

- **Sizing:** the frame sizes itself to its content.
- **Events:** the page receives `navigated`, `exception-decided`, `exception-recommended` and `session-ended`. Only messages from OpenClear's own frame and origin are passed to your page.
- **Use events as hints:** they're for updating your page. Your systems should act on webhooks or the API instead.
- **Framing:** OpenClear only allows itself to be framed by the origins the bank listed, and only inside a session started from a launch URL.
- **Cookies:** sessions use partitioned, `SameSite=None` cookies. If you host OpenClear on a subdomain of your online banking's domain, third-party cookie limits don't apply at all.
