<script lang="ts">
	import "../../app.css";
	import {
		Alert,
		Avatar,
		Banner,
		DarkMode,
		Dropdown,
		DropdownDivider,
		DropdownItem,
		Footer,
		FooterBrand,
		FooterCopyright,
		FooterLink,
		FooterLinkGroup,
		NavBrand,
		Navbar,
	} from "flowbite-svelte";
	import {
		AdjustmentsHorizontalSolid,
		BullhornSolid,
		GithubSolid,
		InfoCircleSolid,
		LockSolid,
		UserEditSolid,
	} from "flowbite-svelte-icons";
	import type { Snippet } from "svelte";
	import { PUBLIC_BACKEND_BASE_URL } from "$env/static/public";
	import {
		type AboutResponse,
		type UserResponse,
		usersLogout,
	} from "$lib/generated";
	import { alerts, auth, routing } from "$lib/utils/global_state.svelte";
	import { get_error_msg } from "$lib/utils/http_utils";

	let dropDownOpen = $state(false);

	type Data = {
		about: AboutResponse;
		user_info: UserResponse;
	};
	interface Props {
		data: Data;
		children: Snippet;
	}
	let { data, children }: Props = $props();

	async function logout(): Promise<void> {
		//send post request and wait for response
		const { error, response } = await usersLogout();
		if (error) {
			if (response.status === 401) {
				//ignore 401 error because we want to logout anyway
				auth.logout();
				return;
			}
			const errorMsg: string = `Error during logout: ${get_error_msg(error)}`;
			alerts.push({ msg: errorMsg, color: "red" });
		} else {
			auth.logout();
		}
	}
</script>
<!-- On smaller screens (height <1250px) the login site will be expanded to the size of the screen pushing the footer outside the screen. Looks better that way-->
<div class={`flex flex-col gap-8 ${routing.location.startsWith("#/auth") ? "min-h-[min(calc(100vh+180px),max(100vh,1250px))]" : "min-h-screen"}`}>
  <header>
    <Navbar class="px-2 sm:px-4 py-1.5 bg-slate-300 dark:bg-slate-900">
      <NavBrand href="#/">
        <!-- self-center: x/&y centering for flex item, whitespace-nowrap: text should not wrap, text-xl/font-semibold: font size/type-->
        <span class="self-center whitespace-nowrap text-xl font-semibold dark:text-white">Project</span>
        <img src="/favicon.png" class="ml-1.5 h-7 sm:h-8" alt="Logo in the form of a W"/>
      </NavBrand>
      <div class="flex gap-2 sm:gap-4 md:order-2">
        <DarkMode/>
        {#if auth.loggedIn}
          <Avatar id="avatar-menu" class="cursor-pointer"/>
        {/if}
      </div>
      {#if auth.loggedIn}
        <Dropdown simple class="whitespace-nowrap" placement="bottom" triggeredBy="#avatar-menu" activeUrl={routing.location} bind:isOpen={dropDownOpen}>
          <DropdownItem href="#/account/info" onclick={() => {dropDownOpen = false}}><UserEditSolid class="inline mr-2"/>Account</DropdownItem>
          <DropdownItem href="#/account/security" onclick={() => {dropDownOpen = false}}><LockSolid class="inline mr-2"/>Security</DropdownItem>
          <DropdownItem href="#/account/default_settings" onclick={() => {dropDownOpen = false}}><AdjustmentsHorizontalSolid class="inline mr-2"/>Default settings</DropdownItem>
          <DropdownDivider />
          <DropdownItem class="cursor-pointer" onclick={() => {dropDownOpen = false; logout()}}>Log out</DropdownItem>
        </Dropdown>
      {/if}
    </Navbar>

    {#each data.about.site_banners as banner}
      <Banner color={banner.urgency >= 200 ? "primary" : "gray"} class={`relative z-10 md:p-3 p-1.5 ${banner.urgency >= 200 ? "bg-primary-500" : ""}`} dismissable={false}>
        <div class={`flowbite-anchors flex items-center gap-3 ${banner.urgency >= 100 && banner.urgency < 200 ? "text-primary-500 dark:text-primary-400" : "text-gray-900 dark:text-white"}`}>
          <BullhornSolid size="sm"/>
          {@html banner.html}
        </div>
      </Banner>
    {/each}

    {#each alerts as alert}
      <Alert color={alert.color} dismissable class="m-2">
        {#snippet icon()}<InfoCircleSolid class="w-4 h-4" />{/snippet}
        {alert.msg}
      </Alert>
    {/each}
  </header>

  <main class="flex-1 flex flex-col mx-4">
    {@render children()}
  </main>

  <Footer class="mx-4 mb-4" footerType="logo">
    <div class="sm:flex sm:items-center sm:justify-between sm:gap-6">
      <FooterBrand href="#/" name="Project">
        <img src="/favicon.png" class="ml-1.5 mr-9 h-7 sm:h-9" alt="Logo in the form of a W"/>
      </FooterBrand>
      <FooterLinkGroup class="flex flex-wrap items-center gap-y-2">
        <FooterLink href="#/about">About</FooterLink>
        {#if data.about.imprint}
          <FooterLink href={data.about.imprint.url != null ? data.about.imprint.url : "#/imprint"}>Imprint</FooterLink>
        {/if}
        {#if Object.keys(data.about.terms_of_services).length !== 0}
          <FooterLink href="#/tos">Terms of Services</FooterLink>
        {/if}
        <FooterLink href={`${PUBLIC_BACKEND_BASE_URL}/docs`} target="_blank" rel="noopener noreferrer">API docs</FooterLink>
        <FooterLink href="https://github.com/ssciwr/project-W" target="_blank" rel="noopener noreferrer"><GithubSolid class="inline mr-2"/>GitHub</FooterLink>
      </FooterLinkGroup>
    </div>
    <hr class="my-6 lg:my-8 border-gray-200 dark:border-gray-700"/>
    <FooterCopyright href="https://github.com/ssciwr/project-W/blob/main/COPYING.md" target="_blank" rel="noopener noreferrer" by="Julian Partanen and contributors (click to see all contributors)." year={2026}/>
  </Footer>
</div>
