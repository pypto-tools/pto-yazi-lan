--- @since 26.9.1
local M = {}

local context = ya.sync(function()
	local tab = cx.active
	local cwd = tab.current.cwd
	if cwd.spec.is_virtual then return end
	local selected = {}
	for _, file in pairs(tab.selected) do
		if file.url.spec.is_virtual then return end
		selected[#selected + 1] = tostring(file.url.path)
	end
	local hovered = tab.current.hovered
	return tostring(cwd.path), selected, hovered and tostring(hovered.url.path)
end)

function M:entry(job)
	local cwd, selected, focus = context()
	if not cwd then
		return ya.notify { title = "pto-yazi-lan", content = "请从本机目录打开传输面板", timeout = 5, level = "warn" }
	end
	local args = { "--local-dir", cwd }
	if job.args[1] then args[#args + 1] = job.args[1] end
	if focus then
		args[#args + 1] = "--focus"
		args[#args + 1] = focus
	end
	for _, path in ipairs(selected) do
		args[#args + 1] = "--select"
		args[#args + 1] = path
	end
	local permit = ui.hide()
	local child, err = Command(os.getenv("YAZI_LAN_BIN") or "pto-yazi-lan"):arg(args)
		:stdin(Command.INHERIT):stdout(Command.INHERIT):stderr(Command.PIPED):spawn()
	local output
	if child then output, err = child:wait_with_output() end
	permit:drop()
	if not output or not output.status.success and output.status.code ~= 130 then
		local message = output and output.stderr or tostring(err)
		ya.notify { title = "pto-yazi-lan", content = message, timeout = 8, level = "error" }
	end
	ya.emit("refresh", {})
end

return M
