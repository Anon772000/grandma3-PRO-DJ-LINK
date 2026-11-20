-- Stops the known CDJ Link listeners, then checks their UDP ports.
-- Never terminates grandMA3 or another application and never changes fixture values.
local PORTS = {50000, 50001, 50002}

local function closeSocket(sock)
    if sock then pcall(function() sock:close() end) end
end

local function main()
    local notes = {}
    local service = rawget(_G, 'CDJLINK')
    if type(service) == 'table' then
        -- Invalidate scheduled callbacks even if the normal stop routine fails.
        service.running = false
        service.generation = (tonumber(service.generation) or 0) + 1
        if type(service.stop) == 'function' then
            local ok, err = pcall(service.stop)
            if not ok then notes[#notes + 1] = 'Normal stop reported: ' .. tostring(err) end
        end
        -- Fallback for a partially failed startup or stop.
        if type(service.sockets) == 'table' then
            for _, sock in pairs(service.sockets) do closeSocket(sock) end
        end
        closeSocket(service.sender)
        service.sockets = {}
        service.sender = nil
        service.sim = nil
        if type(service.engine) == 'table' then service.engine.armed = false end
        notes[#notes + 1] = 'CDJ Link Sync stop requested; output disarmed.'
    else
        notes[#notes + 1] = 'No registered CDJ Link Sync listener found.'
    end

    local vars = GlobalVars()
    for _, key in ipairs({'CDJ_LINKTEST_RUNNING', 'CDJ_DIRECTSYNC_RUNNING'}) do
        local ok, err = pcall(SetVar, vars, key, 0)
        if not ok then notes[#notes + 1] = key .. ': ' .. tostring(err) end
    end
    notes[#notes + 1] = 'Older Link Test / DirectSync stop flags set.'

    -- Give older coroutine listeners time to notice their stop flags and close.
    Timer(function()
        local lines = {}
        for _, text in ipairs(notes) do lines[#lines + 1] = text end
        lines[#lines + 1] = ''
        local loaded, socket = pcall(require, 'socket')
        local constructor = loaded and type(socket) == 'table' and (socket.udp4 or socket.udp)
        local busy, unknown = 0, 0
        if type(constructor) ~= 'function' then
            unknown = #PORTS
            lines[#lines + 1] = 'Port checks unavailable: LuaSocket UDP is not available.'
        else
            for _, port in ipairs(PORTS) do
                local probe
                local ok, bound, reason = pcall(function()
                    probe = assert(constructor())
                    probe:settimeout(0)
                    return probe:setsockname('*', port)
                end)
                closeSocket(probe) -- Release every temporary probe, including failed binds.
                if not ok then
                    unknown = unknown + 1
                    lines[#lines + 1] = 'UDP ' .. port .. ': CHECK FAILED - ' .. tostring(bound)
                elseif bound then
                    lines[#lines + 1] = 'UDP ' .. port .. ': AVAILABLE'
                else
                    busy = busy + 1
                    lines[#lines + 1] = 'UDP ' .. port .. ': UNAVAILABLE - ' .. tostring(reason)
                end
            end
        end
        lines[#lines + 1] = ''
        if busy > 0 or unknown > 0 then
            lines[#lines + 1] = 'An unavailable port can belong to another plugin or application.'
            lines[#lines + 1] = 'If it remains busy, save the show and restart grandMA3 normally.'
        else
            lines[#lines + 1] = 'Start ONE copy of CDJ Link Sync when ready.'
        end
        local text = table.concat(lines, '\n')
        Printf('CDJ Stop All:\n%s', text)
        MessageBox({title = 'CDJ Stop All - Port Check', message = text,
                    commands = {{value = 1, name = 'OK'}}})
    end, 0.3, 1)
end

return main
