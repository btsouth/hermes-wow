-- UI: the agent board. A badge, a panel, a detail pane, a settings pane, and a
-- minimap button.
--
-- Drawn by the game, not laid over it, so nothing can cover it and full screen
-- is fine. Frames are plain and NON-secure: nothing here acts on the game world,
-- so combat lockdown never applies to it.
--
-- Rules this file follows, because the client punishes the alternatives:
--   * fonts are referenced by NAME (passing the font object is a client error)
--   * frames that take a backdrop ask for BackdropTemplate
--   * no OnUpdate except while dragging the minimap button: everything else
--     happens on events and clicks
--   * widgets are built once and reused, so a refresh writes text and toggles
--     what already exists instead of allocating
--   * text that must fit is measured with GetStringWidth, never guessed at by
--     character count: a variable-width font makes "how many letters fit" a
--     lie, and the client clips rather than ellipsises

local addonName, ns = ...

local L = ns.L

local ROW_HEIGHT = 34
local ROW_COUNT = 12
local PANEL_WIDTH = 560
local PANEL_HEIGHT = 540
local ROW_TOP = -96
local MINIMAP_SIZE = 32
local MINIMAP_RADIUS = 80
local SEP = " · "

-- Room the row's right-hand columns take: age, chevron, and the panel padding.
-- The measured widths below are the arbiter; these only bound the search.
local ROW_TEXT_WIDTH = PANEL_WIDTH - 20 - 16 - 84
local DETAIL_TEXT_WIDTH = PANEL_WIDTH - 20 - 24 - 24
local BADGE_TEXT_WIDTH = 110

-- Font object names every client family registers; "muted" is done with colour
-- instead of the disable faces, which do vary.
local FONT_TITLE = "GameFontNormal"
local FONT_BODY = "GameFontHighlightSmall"
local FONT_SMALL = "GameFontNormalSmall"

local MUTED = { 0.58, 0.62, 0.72 }
local DIM = { 0.44, 0.47, 0.56 }
local TEXT = { 0.91, 0.93, 0.97 }
local GOLD = { 0.78, 0.64, 0.35 }
local ELLIPSIS = "..."

-- The dark Hermes skin from the spec, and the plain alternative. Both are colour
-- textures, so there is no shipped art to break at an odd UI scale.
local THEMES = {
  dark = {
    name = "dark",
    bg = { 0.051, 0.071, 0.125, 0.94 },
    edge = { 0.184, 0.420, 0.847, 1 },
    row = { 0.078, 0.106, 0.176, 0.55 },
    rowAlt = { 0.063, 0.086, 0.145, 0.55 },
    highlight = { 0.184, 0.420, 0.847, 0.30 },
    button = { 0.086, 0.118, 0.196, 0.95 },
    buttonHover = { 0.145, 0.204, 0.333, 0.95 },
    buttonActive = { 0.184, 0.420, 0.847, 0.55 },
    buttonEdge = { 0.235, 0.290, 0.400, 1 },
    buttonText = { 0.82, 0.86, 0.94 },
    field = { 0.031, 0.043, 0.078, 0.85 },
  },
  classic = {
    name = "classic",
    bg = { 0.05, 0.05, 0.05, 0.90 },
    edge = { 0.60, 0.60, 0.60, 1 },
    row = { 0.11, 0.11, 0.11, 0.55 },
    rowAlt = { 0.08, 0.08, 0.08, 0.55 },
    highlight = { 0.30, 0.30, 0.30, 0.60 },
    button = { 0.16, 0.16, 0.16, 0.95 },
    buttonHover = { 0.24, 0.24, 0.24, 0.95 },
    buttonActive = { 0.36, 0.36, 0.36, 0.95 },
    buttonEdge = { 0.42, 0.42, 0.42, 1 },
    buttonText = { 0.86, 0.86, 0.86 },
    field = { 0.02, 0.02, 0.02, 0.85 },
  },
}

local BACKDROP = {
  bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
  edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
  tile = true,
  tileSize = 16,
  edgeSize = 12,
  insets = { left = 3, right = 3, top = 3, bottom = 3 },
}

local function theme()
  return THEMES[(HermesAIDB and HermesAIDB.theme) or "dark"] or THEMES.dark
end

--- CreateFontString with a fallback: a client that does not register the
--- preferred face gets a plain label instead of an error frame.
local function fontString(parent, layer, template)
  local ok, label = pcall(parent.CreateFontString, parent, nil, layer, template)
  if ok and label then
    return label
  end
  return parent:CreateFontString(nil, layer, "GameFontNormalSmall")
end

local function colorize(label, color)
  label:SetTextColor(color[1], color[2], color[3])
  return label
end

--- A colour texture behind a frame that wants a backdrop, when it has none.
---
--- On a client without the BackdropTemplate mixin the frame simply has no
--- SetBackdrop, and an error frame on login is a worse outcome than a panel
--- without its border. The art is drawn with colour textures, so nothing else
--- depends on the template.
local function backdropFrame(kind, name, parent, template)
  local ok, frame = pcall(CreateFrame, kind, name, parent, template)
  if ok and frame then
    return frame
  end
  return CreateFrame(kind, name, parent)
end

local function applyBackdrop(frame, alpha, background, edge)
  if not frame or not frame.SetBackdrop then
    return false
  end

  frame:SetBackdrop(BACKDROP)
  local palette = theme()
  local fill = background or palette.bg
  frame:SetBackdropColor(fill[1], fill[2], fill[3], alpha or fill[4])
  local line = edge or palette.edge
  frame:SetBackdropBorderColor(line[1], line[2], line[3], line[4] or 1)
  return true
end

--- A saved position, or the default.
---
--- These values come back from SavedVariables, which is a file the player can
--- edit and a patch can leave stale: a string or a short table here would raise
--- "Usage: SetPoint(...)" during BuildBadge and take the whole addon with it.
local function savedPoint(value, default)
  if type(value) == "table" and type(value[1]) == "string"
    and tonumber(value[2]) and tonumber(value[3]) then
    return value
  end
  return default
end

--- The longest prefix of `text` that fits `maxWidth`, with an ellipsis when it
--- had to be cut. One measurement for text that fits, a handful for text that
--- does not, and never a guess from a character count.
local function fitText(label, text, maxWidth)
  text = tostring(text or "")
  label:SetText(text)

  if maxWidth <= 0 or not label.GetStringWidth then
    return text
  end
  if label:GetStringWidth() <= maxWidth then
    return text
  end

  local best = 1
  local low, high = 1, #text
  while low <= high do
    local middle = math.floor((low + high) / 2)
    label:SetText(string.sub(text, 1, middle) .. ELLIPSIS)
    if label:GetStringWidth() <= maxWidth then
      best = middle
      low = middle + 1
    else
      high = middle - 1
    end
  end

  -- Prefer cutting at a space, a slash or a hyphen: "Review the Lua addon
  -- code..." reads as trimmed, and so does ".../Projects/hermes-wow" -- whereas
  -- ".../hom" reads as data loss.
  local withBoundary = string.sub(text, 1, best):match("^(.*)[%s/%-]%S*$")
  if withBoundary and #withBoundary >= math.floor(best * 0.6) then
    best = #withBoundary
  end

  local fitted = string.sub(text, 1, best) .. ELLIPSIS
  label:SetText(fitted)
  return fitted
end

--- Hard cap for text going somewhere that wraps (tooltips, the preview line):
--- wrapping handles the width, this only stops a wall of text from swallowing
--- the frame.
local function shorten(text, limit)
  text = tostring(text or "")
  if #text <= limit then
    return text
  end

  local head = string.sub(text, 1, limit - #ELLIPSIS)
  -- Cut at the last sentence or line end if one is close by: "...green on 3.10"
  -- reads as trimmed, "...green on 3.10/3.1" reads as broken.
  local boundary = head:match("^.*[%.!?%c]%s?")
  if boundary and #boundary >= math.floor(limit * 0.5) then
    head = boundary:gsub("%s+$", "")
  end
  return head .. ELLIPSIS
end

local function makeDot(parent, size)
  local dot = parent:CreateTexture(nil, "ARTWORK")
  dot:SetSize(size or 7, size or 7)
  dot:SetColorTexture(0.5, 0.5, 0.5, 1)
  return dot
end

--- A flat button in the panel's own colours.
---
--- Deliberately NOT `UIPanelButtonTemplate`: that draws the client's beige
--- buttons, which fight a dark panel and would be the only part of this window
--- not under the theme's control. Four textures and a label are cheaper than a
--- template and stay consistent when the skin changes.
local function makeButton(parent, width, height, label, onClick)
  local button = CreateFrame("Button", nil, parent)
  button:SetSize(width, height)
  button:EnableMouse(true)

  local background = button:CreateTexture(nil, "BACKGROUND")
  background:SetAllPoints()
  button.background = background

  button.label = colorize(fontString(button, "OVERLAY", FONT_SMALL), theme().buttonText)
  button.label:SetPoint("CENTER", 0, 0)
  button.label:SetText(label or "")

  button:SetScript("OnEnter", function(self)
    self.hovered = true
    ns:SkinButton(self)
  end)
  button:SetScript("OnLeave", function(self)
    self.hovered = false
    ns:SkinButton(self)
  end)
  if onClick then
    button:SetScript("OnClick", onClick)
  end

  ns:SkinButton(button)
  return button
end

--- One place decides how a button looks, so hover, active and normal can never
--- disagree with each other or with the theme.
function ns:SkinButton(button)
  local palette = theme()
  local color = button.active and palette.buttonActive
    or (button.hovered and palette.buttonHover or palette.button)

  button.background:SetColorTexture(color[1], color[2], color[3], color[4] or 1)
  button.label:SetTextColor(palette.buttonText[1], palette.buttonText[2], palette.buttonText[3])
end

local function setButtonText(button, text)
  button.text = text
  button.label:SetText(text)
end

local function setButtonActive(button, active)
  if button.active ~= active then
    button.active = active
    ns:SkinButton(button)
  end
end

-- ------------------------------------------------------------------- badge --

function ns:BuildBadge()
  if self.Badge then
    return self.Badge
  end

  local badge = backdropFrame("Frame", "HermesAIBadge", UIParent, "BackdropTemplate")
  badge:SetSize(190, 30)
  badge:SetFrameStrata("MEDIUM")
  badge:SetClampedToScreen(true)
  badge:SetMovable(true)
  badge:EnableMouse(true)
  badge:RegisterForDrag("LeftButton")
  applyBackdrop(badge)

  local point = savedPoint(HermesAIDB.point, ns.DEFAULT_BADGE_POINT)
  badge:SetPoint(point[1], UIParent, point[1], point[2], point[3])

  badge:SetScript("OnDragStart", function(self)
    self:StartMoving()
  end)
  badge:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    local anchor, _, x, y = self:GetPoint()
    HermesAIDB.point = { anchor, math.floor(x + 0.5), math.floor(y + 0.5) }
  end)
  badge:SetScript("OnMouseUp", function(_, button)
    if button == "LeftButton" then
      ns:ToggleBoard()
    end
  end)

  local crest = colorize(fontString(badge, "OVERLAY", FONT_TITLE), GOLD)
  crest:SetPoint("LEFT", 8, 0)
  crest:SetText("H")

  local text = fontString(badge, "OVERLAY", FONT_TITLE)
  text:SetPoint("LEFT", crest, "RIGHT", 6, 0)
  text:SetJustifyH("LEFT")
  badge.text = text

  -- Four dots is what fits beside the words at this size. Which four is decided
  -- per refresh from what is actually there, so a busy board shows the loudest
  -- four rather than the first four of a fixed list.
  local dots = {}
  for index = 1, 4 do
    local item = makeDot(badge, 7)
    item:SetPoint("RIGHT", badge, "RIGHT", -10 - (index - 1) * 11, 0)
    item:Hide()
    dots[index] = item
  end
  badge.dots = dots

  self.Badge = badge
  return badge
end

--- Counts per status, quiet buckets dropped: the dots and the badge words are two
--- channels for the same fact, so a colour-blind player loses nothing.
function ns:BusyStatuses()
  local counts = self:Data().counts or {}
  local out = {}
  for _, status in ipairs(self.STATUS_ORDER) do
    local count = tonumber(counts[status]) or 0
    if count > 0 and status ~= "idle" and status ~= "finished" then
      out[#out + 1] = { status = status, count = count }
    end
  end
  return out
end

function ns:RefreshBadge()
  local badge = self:BuildBadge()
  local attention = self:Attention()
  local age = self:AgeLabel(self:SnapshotAge())

  if attention > 0 then
    local text = attention == 1 and L["1 needs you"] or ns.Lf("%d need you", attention)
    if self:NewCount() > 0 then
      text = text .. ns.Lf("  +%d new", self:NewCount())
    end
    fitText(badge.text, text .. "  " .. age, BADGE_TEXT_WIDTH)
    badge.text:SetTextColor(unpack(self.STATUS_COLORS.needs))
    badge:SetBackdropBorderColor(unpack(self.STATUS_COLORS.needs))
  else
    local working = tonumber((self:Data().counts or {}).working) or 0
    if working > 0 then
      fitText(badge.text, ns.Lf("%d working  %s", working, age), BADGE_TEXT_WIDTH)
      badge.text:SetTextColor(unpack(self.STATUS_COLORS.working))
    else
      fitText(badge.text, L["idle"] .. "  " .. age, BADGE_TEXT_WIDTH)
      badge.text:SetTextColor(MUTED[1], MUTED[2], MUTED[3])
    end
    badge:SetBackdropBorderColor(GOLD[1], GOLD[2], GOLD[3], 1)
  end

  local busy = self:BusyStatuses()
  for index, dot in ipairs(badge.dots) do
    local entry = busy[index]
    if entry then
      if dot.status ~= entry.status then
        dot.status = entry.status
        dot:SetColorTexture(self:StatusColor(entry.status))
      end
      dot:Show()
    else
      dot.status = nil
      dot:Hide()
    end
  end
end

-- ------------------------------------------------------------ minimap button --

function ns:BuildMinimapButton()
  if self.MinimapButton or not Minimap then
    return self.MinimapButton
  end

  local button = CreateFrame("Button", "HermesAIMinimapButton", Minimap)
  button:SetSize(MINIMAP_SIZE, MINIMAP_SIZE)
  button:SetFrameStrata("MEDIUM")
  button:EnableMouse(true)
  button:RegisterForDrag("LeftButton")
  button:RegisterForClicks("AnyUp")
  button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

  local background = button:CreateTexture(nil, "BACKGROUND")
  background:SetSize(MINIMAP_SIZE, MINIMAP_SIZE)
  background:SetPoint("CENTER")
  button.background = background

  local crest = colorize(fontString(button, "OVERLAY", FONT_TITLE), GOLD)
  crest:SetPoint("CENTER", -1, 1)
  crest:SetText("H")

  local badge = colorize(fontString(button, "OVERLAY", FONT_SMALL), TEXT)
  badge:SetPoint("BOTTOMRIGHT", 4, 1)
  badge:SetText("")
  button.badge = badge

  button:SetScript("OnClick", function(_, mouseButton)
    if mouseButton == "RightButton" then
      ns:Sync("minimap")
    else
      ns:ToggleBoard()
    end
  end)

  -- Dragging is the one place an OnUpdate is justified: nothing else can follow
  -- the cursor, and it exists only for the duration of the drag.
  button:SetScript("OnDragStart", function(self)
    self:SetScript("OnUpdate", function()
      if not Minimap or not GetCursorPosition then
        return
      end
      local mx, my = Minimap:GetCenter()
      local px, py = GetCursorPosition()
      local scale = Minimap:GetEffectiveScale()
      px, py = px / scale, py / scale
      HermesAIDB.minimapAngle = math.deg(math.atan2(py - my, px - mx))
      ns:PositionMinimapButton()
    end)
  end)
  button:SetScript("OnDragStop", function(self)
    self:SetScript("OnUpdate", nil)
  end)
  button:SetScript("OnEnter", function(self)
    if GameTooltip then
      GameTooltip:SetOwner(self, "ANCHOR_LEFT")
      GameTooltip:AddLine(L["Hermes agents"], TEXT[1], TEXT[2], TEXT[3])
      GameTooltip:AddLine(L["left click: the board. right click: sync. drag: move."], 0.7, 0.7, 0.7)
      GameTooltip:Show()
    end
  end)
  button:SetScript("OnLeave", function()
    if GameTooltip then
      GameTooltip:Hide()
    end
  end)

  self.MinimapButton = button
  self:PositionMinimapButton()
  self:SkinMinimapButton()
  button:SetShown(HermesAIDB.minimap ~= false)
  return button
end

function ns:PositionMinimapButton()
  local button = self.MinimapButton
  if not button or not Minimap then
    return
  end

  -- Placed on a ring around the minimap rather than at a corner, which is how
  -- every other minimap button behaves and what players expect to drag.
  local angle = math.rad(tonumber(HermesAIDB and HermesAIDB.minimapAngle) or 210)
  button:ClearAllPoints()
  button:SetPoint("CENTER", Minimap, "CENTER", math.cos(angle) * MINIMAP_RADIUS, math.sin(angle) * MINIMAP_RADIUS)
end

function ns:SkinMinimapButton()
  local button = self.MinimapButton
  if not button or not button.background then
    return
  end
  local palette = theme()
  button.background:SetColorTexture(palette.bg[1], palette.bg[2], palette.bg[3], 1)
end

function ns:RefreshMinimapButton()
  local button = self:BuildMinimapButton()
  if not button then
    return
  end

  button:SetShown(HermesAIDB.minimap ~= false)

  local attention = self:Attention()
  if attention > 0 then
    button.badge:SetText(tostring(attention))
    button.badge:SetTextColor(unpack(self.STATUS_COLORS.needs))
  else
    local working = tonumber((self:Data().counts or {}).working) or 0
    if working > 0 then
      button.badge:SetText(tostring(working))
      button.badge:SetTextColor(unpack(self.STATUS_COLORS.working))
    else
      button.badge:SetText("")
    end
  end
end

-- ------------------------------------------------------------------- panel --

function ns:BuildPanel()
  if self.Board then
    return self.Board
  end

  local panel = backdropFrame("Frame", "HermesAIPanel", UIParent, "BackdropTemplate")
  panel:SetSize(PANEL_WIDTH, PANEL_HEIGHT)
  panel:SetFrameStrata("DIALOG")
  panel:SetClampedToScreen(true)
  panel:SetMovable(true)
  panel:EnableMouse(true)
  panel:EnableMouseWheel(true)
  panel:RegisterForDrag("LeftButton")
  applyBackdrop(panel, 0.96)

  -- Escape closes it like any other client window, and hiding the panel hides its
  -- panes, so no stale detail pane waits behind a closed board.
  if UISpecialFrames then
    local found = false
    for _, name in ipairs(UISpecialFrames) do
      found = found or name == "HermesAIPanel"
    end
    if not found then
      table.insert(UISpecialFrames, "HermesAIPanel")
    end
  end

  local point = savedPoint(HermesAIDB.panelPoint, ns.DEFAULT_PANEL_POINT)
  panel:SetPoint(point[1], UIParent, point[1], point[2], point[3])

  panel:SetScript("OnDragStart", function(self)
    self:StartMoving()
  end)
  panel:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    local anchor, _, x, y = self:GetPoint()
    HermesAIDB.panelPoint = { anchor, math.floor(x + 0.5), math.floor(y + 0.5) }
  end)
  panel:SetScript("OnMouseWheel", function(_, delta)
    ns:ScrollRows(delta)
  end)
  panel:SetScript("OnHide", function()
    ns.offset = 0
    ns:CloseDetail()
    if ns.Settings then
      ns.Settings:Hide()
    end
  end)

  -- header
  local crest = colorize(fontString(panel, "OVERLAY", FONT_TITLE), GOLD)
  crest:SetPoint("TOPLEFT", 12, -11)
  crest:SetText("H")

  local title = colorize(fontString(panel, "OVERLAY", FONT_TITLE), TEXT)
  title:SetPoint("LEFT", crest, "RIGHT", 6, 0)
  title:SetText(L["Hermes Agents"])
  panel.title = title

  local attention = colorize(fontString(panel, "OVERLAY", FONT_BODY), ns.STATUS_COLORS.needs)
  attention:SetPoint("LEFT", title, "RIGHT", 8, 0)
  panel.attention = attention

  local synced = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  synced:SetPoint("LEFT", attention, "RIGHT", 10, 0)
  synced:SetWidth(160)
  synced:SetJustifyH("LEFT")
  synced:SetWordWrap(false)
  panel.synced = synced

  local close = makeButton(panel, 22, 18, "X", function()
    ns:HideBoard()
  end)
  close:SetPoint("TOPRIGHT", -8, -9)
  panel.close = close

  local minimise = makeButton(panel, 22, 18, "-", function()
    ns:ToggleCollapsed()
  end)
  minimise:SetPoint("RIGHT", close, "LEFT", -4, 0)
  panel.minimise = minimise

  local settingsButton = makeButton(panel, 22, 18, "*", function()
    ns:ToggleSettings()
  end)
  settingsButton:SetPoint("RIGHT", minimise, "LEFT", -4, 0)
  panel.settingsButton = settingsButton

  -- search: a plain field over the panel, with its own inset box so the text does
  -- not float on the panel colour
  local field = backdropFrame("Frame", nil, panel, "BackdropTemplate")
  field:SetPoint("TOPLEFT", 12, -40)
  field:SetPoint("TOPRIGHT", -12, -40)
  field:SetHeight(22)
  applyBackdrop(field, nil, theme().field, theme().buttonEdge)
  panel.searchField = field

  local search = CreateFrame("EditBox", "HermesAISearch", panel, "InputBoxTemplate")
  search:SetHeight(22)
  search:SetPoint("TOPLEFT", 12, -40)
  search:SetPoint("TOPRIGHT", -12, -40)
  search:SetAutoFocus(false)
  search:SetTextInsets(8, 6, 0, 0)
  search:SetScript("OnEscapePressed", function(self)
    self:SetText("")
    ns:SetSearch("")
    self:ClearFocus()
  end)
  search:SetScript("OnTextChanged", function(self, userInput)
    if userInput then
      ns:SetSearch(self:GetText())
    end
  end)
  local placeholder = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  placeholder:SetPoint("LEFT", search, "LEFT", 10, 0)
  placeholder:SetText(L["Search agents, threads, or projects..."])
  placeholder:SetShown(true)
  panel.search = search
  panel.placeholder = placeholder

  -- tabs
  panel.tabs = {}
  local previous
  for index, tab in ipairs(ns.TABS) do
    local button = makeButton(panel, 84, 20, tab.label, nil)
    local key = tab.key
    button:SetScript("OnClick", function()
      ns:SetTab(key)
    end)
    if previous then
      button:SetPoint("LEFT", previous, "RIGHT", 4, 0)
    else
      button:SetPoint("TOPLEFT", 12, -68)
    end
    panel.tabs[index] = button
    previous = button
  end

  -- rows: built once. Two background textures exist so a refresh shows one and
  -- hides the other instead of writing a colour every time.
  panel.rows = {}
  for index = 1, ROW_COUNT do
    local row = CreateFrame("Button", nil, panel)
    row:SetHeight(ROW_HEIGHT)
    row:SetPoint("TOPLEFT", 10, ROW_TOP - (index - 1) * ROW_HEIGHT)
    row:SetPoint("RIGHT", panel, "RIGHT", -10, 0)

    row.even = row:CreateTexture(nil, "BACKGROUND")
    row.even:SetAllPoints()
    row.even:Hide()

    row.odd = row:CreateTexture(nil, "BACKGROUND")
    row.odd:SetAllPoints()

    row.highlight = row:CreateTexture(nil, "HIGHLIGHT")
    row.highlight:SetAllPoints()

    row.dot = makeDot(row, 8)
    row.dot:SetPoint("LEFT", 6, 0)

    row.title = colorize(fontString(row, "OVERLAY", FONT_BODY), TEXT)
    row.title:SetPoint("TOPLEFT", row.dot, "TOPRIGHT", 8, -3)
    row.title:SetWidth(ROW_TEXT_WIDTH)
    row.title:SetJustifyH("LEFT")
    row.title:SetWordWrap(false)

    row.meta = colorize(fontString(row, "OVERLAY", FONT_SMALL), MUTED)
    row.meta:SetPoint("TOPLEFT", row.title, "BOTTOMLEFT", 0, -1)
    row.meta:SetWidth(ROW_TEXT_WIDTH)
    row.meta:SetJustifyH("LEFT")
    row.meta:SetWordWrap(false)

    row.age = colorize(fontString(row, "OVERLAY", FONT_SMALL), DIM)
    row.age:SetPoint("RIGHT", row, "RIGHT", -26, 0)

    row.chevron = colorize(fontString(row, "OVERLAY", FONT_BODY), MUTED)
    row.chevron:SetPoint("RIGHT", row, "RIGHT", -8, 0)
    row.chevron:SetText(">")

    row:SetScript("OnClick", function()
      ns:OpenDetail(row.session, true)
    end)
    row:SetScript("OnEnter", function()
      ns:ShowRowTooltip(row)
    end)
    row:SetScript("OnLeave", function()
      if GameTooltip then
        GameTooltip:Hide()
      end
    end)

    panel.rows[index] = row
  end

  -- footer: the legend on the left, and on the right anything the list itself has
  -- to admit to (a window range, or a machine that did not answer)
  local legend = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  legend:SetPoint("BOTTOMLEFT", 12, 8)
  legend:SetText(ns:LegendText())
  panel.legend = legend

  local hosts = colorize(fontString(panel, "OVERLAY", FONT_SMALL), DIM)
  hosts:SetPoint("BOTTOMRIGHT", -12, 8)
  hosts:SetWidth(230)
  hosts:SetJustifyH("RIGHT")
  hosts:SetWordWrap(false)
  panel.hosts = hosts

  panel:Hide()
  self.Board = panel
  return panel
end

--- Empty and degraded states, which must never read as "your agents are idle".
function ns:PanelStatusLine()
  if self:IsMissing() then
    return L["no snapshot yet: run hermes-wow wow install on this machine, then sync"]
  end
  if self.loaded and self.loaded.incompatible then
    return ns.Lf("bridge speaks payload v%d, this addon speaks v%d: update whichever is older",
      tonumber(self.loaded.schema) or 0, self.PAYLOAD_SCHEMA)
  end
  if self:IsStale() then
    return ns.Lf("bridge payload unusable: showing the last good snapshot, synced %s",
      self:AgeLabel(self:SnapshotAge()))
  end
  return nil
end

--- The words for the statuses the badge dots can show, in the same order and
--- from the same filter. A legend that lists "finished" while the dots never
--- draw it, and omits "error" while they do, describes nothing.
function ns:LegendText()
  local parts = {}
  for _, status in ipairs(self.STATUS_ORDER) do
    if status ~= "idle" and status ~= "finished" then
      parts[#parts + 1] = string.lower(self:StatusLabel(status))
    end
  end
  return table.concat(parts, " / ")
end

--- Is this session's machine out of reach?
---
--- Two sources can say so: the per-row flag the bridge sets while merging, and
--- the host map in the header. Either is enough, because a row that contradicts
--- the footer line about its own machine is worse than a row that is merely
--- dimmed.
function ns:IsOffline(session)
  if not session then
    return false
  end
  if session.offline then
    return true
  end
  local host = session.host
  if not host or host == "local" then
    return false
  end
  return self:HostStatus()[host] ~= "ok"
end

--- Paint the rows for the current tab, search and offset.
---
--- One pass over everything that can change, with a cheap "did this actually
--- change" guard on the textures: the panel is refreshed on every click and
--- every keypress in the search box, and re-setting the same colour 12 times per
--- keystroke is the sort of thing that shows up as input lag in a raid.
function ns:RefreshPanel()
  local panel = self:BuildPanel()
  local sessions = self:Filtered()
  local attention = self:Attention()
  local palette = theme()

  -- The list can shrink under the window (a new snapshot, a narrower search).
  local maxOffset = math.max(0, #sessions - ROW_COUNT)
  self.offset = math.min(maxOffset, math.max(0, self.offset or 0))

  local attentionText = attention > 0
    and (attention == 1 and L["1 needs you"] or ns.Lf("%d need you", attention))
    or L["all clear"]
  if panel.attentionText ~= attentionText then
    panel.attentionText = attentionText
    fitText(panel.attention, attentionText, PANEL_WIDTH - 260)
  end
  panel.attention:SetTextColor(unpack(attention > 0 and self.STATUS_COLORS.needs or self.STATUS_COLORS.reply))

  local synced = ns.Lf("synced %s", self:AgeLabel(self:SnapshotAge()))
  if self:NewCount() > 0 then
    synced = ns.Lf("+%d new, %s", self:NewCount(), synced)
  end
  fitText(panel.synced, synced, 160)

  -- Where you are in the list, then who is down. A list longer than the panel
  -- must say so, or the missing rows look like they do not exist.
  local trailer = {}
  if #sessions > ROW_COUNT then
    trailer[#trailer + 1] = ns.Lf("%d-%d of %d, scroll",
      self.offset + 1, math.min(self.offset + ROW_COUNT, #sessions), #sessions)
  end
  local offline = self:OfflineHosts()
  if #offline > 0 then
    trailer[#trailer + 1] = L["host offline: "] .. table.concat(offline, ", ")
  end
  fitText(panel.hosts, table.concat(trailer, "  "), 230)

  local notice = self:PanelStatusLine()
  -- The footer is shared between the legend and the range/host trailer. These
  -- two caps add up to less than the footer's inner width, so neither can run
  -- under the other however long a host list gets.
  fitText(panel.legend, notice or self:LegendText(), 300)

  local activeKey = self:Tab().key
  for index, tab in ipairs(ns.TABS) do
    local button = panel.tabs[index]
    if button then
      local count = self:TabCount(tab)
      local label = count > 0 and (tab.label .. " " .. count) or tab.label
      if button.text ~= label then
        setButtonText(button, label)
        fitText(button.label, label, 78)
      end
      setButtonActive(button, tab.key == activeKey)
    end
  end

  local collapsed = self.collapsed == true

  -- The search box and the tab row are anchored below a 56px bar when collapsed,
  -- so they have to go with the rows they filter.
  panel.searchField:SetShown(not collapsed)
  panel.search:SetShown(not collapsed)
  panel.placeholder:SetShown(not collapsed and (self.search or "") == "")
  for _, button in ipairs(panel.tabs) do
    button:SetShown(not collapsed)
  end

  for index, row in ipairs(panel.rows) do
    -- Explicit, not `collapsed and nil or ...`: that idiom returns the session
    -- when collapsed is true, which is exactly what it looks like it prevents.
    local session
    if not collapsed then
      session = sessions[self.offset + index]
    end
    row.session = session

    if session then
      local offline = self:IsOffline(session)
      local r, g, b = self:StatusColor(session.status)
      local dotKey = session.status .. (offline and "-off" or "")
      if row.dotKey ~= dotKey then
        row.dotKey = dotKey
        row.dot:SetColorTexture(r, g, b, offline and 0.45 or 1)
      end

      local title = session.title or ""
      if row.titleText ~= title or row.titleOffline ~= offline then
        row.titleText = title
        row.titleOffline = offline
        fitText(row.title, title, ROW_TEXT_WIDTH)
        if offline then
          row.title:SetTextColor(DIM[1], DIM[2], DIM[3])
        else
          row.title:SetTextColor(TEXT[1], TEXT[2], TEXT[3])
        end
      end

      local meta = self:SessionTrail(session, offline)
      if row.metaText ~= meta then
        row.metaText = meta
        fitText(row.meta, meta, ROW_TEXT_WIDTH)
      end
      row.meta:SetTextColor(r, g, b)

      local age = self:ShortAge(session.age)
      if row.ageText ~= age then
        row.ageText = age
        row.age:SetText(age)
      end

      if index % 2 == 0 then
        row.even:Show()
        row.odd:Hide()
      else
        row.even:Hide()
        row.odd:Show()
      end
      row:Show()
    else
      row.session = nil
      row:Hide()
    end
  end

  -- Row striping and the hover wash follow the theme, so a skin change is one
  -- call rather than a full rebuild.
  for index, row in ipairs(panel.rows) do
    local odd = index % 2 == 1
    local color = odd and palette.row or palette.rowAlt
    local texture = odd and row.odd or row.even
    if texture.theme ~= palette.name then
      texture:SetColorTexture(color[1], color[2], color[3], color[4] or 1)
      texture.theme = palette.name
    end
    if row.highlightTheme ~= palette.name then
      row.highlight:SetColorTexture(unpack(palette.highlight))
      row.highlightTheme = palette.name
    end
  end
end

--- The row's second line: status, then where it lives, then what it is doing.
function ns:SessionTrail(session, offline)
  local segments = { session.label or self:StatusLabel(session.status) }

  local trail = (session.host and session.host ~= "local") and session.host or session.profile
  if trail and trail ~= "" then
    segments[#segments + 1] = trail
  end
  if session.project and session.project ~= "" then
    segments[#segments + 1] = session.project
  end

  if offline == nil then
    offline = self:IsOffline(session)
  end
  if offline then
    segments[#segments + 1] = L["host offline"]
  elseif session.activity and session.activity ~= "" then
    segments[#segments + 1] = session.activity
  end

  return table.concat(segments, SEP)
end

function ns:ShowRowTooltip(row)
  local session = row.session
  if not session or not GameTooltip then
    return
  end

  GameTooltip:SetOwner(row, "ANCHOR_RIGHT")
  GameTooltip:AddLine(shorten(session.title, 90), TEXT[1], TEXT[2], TEXT[3], true)
  GameTooltip:AddLine(session.label or self:StatusLabel(session.status), unpack(ns.STATUS_COLORS[session.status] or DIM))

  local where = self:WhereText(session, " / ")
  if where ~= "" then
    GameTooltip:AddLine(where, 0.7, 0.7, 0.7)
  end

  GameTooltip:AddLine(ns.Lf("last activity %s", self:AgeLabel(session.age)), 0.6, 0.6, 0.6)
  if session.id then
    GameTooltip:AddLine(session.id, 0.5, 0.5, 0.5)
  end
  if session.preview and session.preview ~= "" then
    GameTooltip:AddLine(shorten(session.preview, 140), 0.8, 0.8, 0.8, true)
  end
  GameTooltip:AddLine(L["click for detail, or /hermesai reply"], 0.5, 0.5, 0.5)
  GameTooltip:Show()
end

--- Where a session lives, as one string: machine, profile, project.
function ns:WhereText(session, separator)
  local where = {
    (session.host and session.host ~= "local") and session.host or L["this machine"],
  }
  if session.profile and session.profile ~= "" then
    where[#where + 1] = session.profile
  end
  if session.project and session.project ~= "" then
    where[#where + 1] = session.project
  end
  return table.concat(where, separator or " / ")
end

-- ------------------------------------------------------------- detail pane --

function ns:BuildDetail()
  if self.Detail then
    return self.Detail
  end

  local panel = self:BuildPanel()
  local detail = backdropFrame("Frame", "HermesAIDetail", panel, "BackdropTemplate")
  detail:SetPoint("TOPLEFT", 10, ROW_TOP)
  detail:SetPoint("BOTTOMRIGHT", -10, 28)
  detail:EnableMouse(true)
  applyBackdrop(detail, 0.97)

  detail.title = colorize(fontString(detail, "OVERLAY", FONT_TITLE), TEXT)
  detail.title:SetPoint("TOPLEFT", 12, -10)
  detail.title:SetWidth(DETAIL_TEXT_WIDTH)
  detail.title:SetJustifyH("LEFT")
  detail.title:SetWordWrap(false)

  detail.meta = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.meta:SetPoint("TOPLEFT", detail.title, "BOTTOMLEFT", 0, -2)
  detail.meta:SetWidth(DETAIL_TEXT_WIDTH)
  detail.meta:SetJustifyH("LEFT")
  detail.meta:SetWordWrap(false)

  detail.status = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.status:SetPoint("TOPLEFT", detail.meta, "BOTTOMLEFT", 0, -8)
  detail.status:SetWidth(DETAIL_TEXT_WIDTH)
  detail.status:SetJustifyH("LEFT")
  detail.status:SetWordWrap(false)

  detail.preview = colorize(fontString(detail, "OVERLAY", FONT_BODY), TEXT)
  detail.preview:SetPoint("TOPLEFT", detail.status, "BOTTOMLEFT", 0, -10)
  detail.preview:SetWidth(DETAIL_TEXT_WIDTH)
  detail.preview:SetJustifyH("LEFT")

  detail.stats = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
  detail.stats:SetPoint("TOPLEFT", detail.preview, "BOTTOMLEFT", 0, -12)
  detail.stats:SetWidth(DETAIL_TEXT_WIDTH)
  detail.stats:SetJustifyH("LEFT")
  detail.stats:SetWordWrap(false)

  detail.target = colorize(fontString(detail, "OVERLAY", FONT_SMALL), DIM)
  detail.target:SetPoint("TOPLEFT", detail.stats, "BOTTOMLEFT", 0, -6)
  detail.target:SetWidth(DETAIL_TEXT_WIDTH)
  detail.target:SetJustifyH("LEFT")
  detail.target:SetWordWrap(false)

  -- The pane is taller than one preview line needs. Rather than leave a hole
  -- above the composer, the facts a player would otherwise have to guess at fill
  -- it: two columns of key/value pairs, built once and rewritten on open.
  detail.facts = {}
  local COLUMN_WIDTH = math.floor(DETAIL_TEXT_WIDTH / 2)
  for index = 1, 6 do
    local column = (index - 1) % 2
    local row = math.floor((index - 1) / 2)

    local key = colorize(fontString(detail, "OVERLAY", FONT_SMALL), DIM)
    key:SetPoint("TOPLEFT", detail.target, "BOTTOMLEFT", column * COLUMN_WIDTH, -12 - row * 15)
    key:SetWidth(64)
    key:SetJustifyH("LEFT")
    key:SetWordWrap(false)

    local value = colorize(fontString(detail, "OVERLAY", FONT_SMALL), MUTED)
    value:SetPoint("LEFT", key, "RIGHT", 4, 0)
    value:SetWidth(COLUMN_WIDTH - 72)
    value:SetJustifyH("LEFT")
    value:SetWordWrap(false)

    detail.facts[index] = { key = key, value = value }
  end

  local close = makeButton(detail, 22, 18, "X", function()
    ns:CloseDetail()
  end)
  close:SetPoint("TOPRIGHT", -8, -8)

  local field = backdropFrame("Frame", nil, detail, "BackdropTemplate")
  field:SetPoint("BOTTOMLEFT", 12, 44)
  field:SetPoint("RIGHT", detail, "RIGHT", -126, 0)
  field:SetHeight(22)
  applyBackdrop(field, nil, theme().field, theme().buttonEdge)
  detail.composerField = field

  local composer = CreateFrame("EditBox", "HermesAIComposer", detail, "InputBoxTemplate")
  composer:SetHeight(22)
  composer:SetPoint("BOTTOMLEFT", 12, 44)
  composer:SetPoint("RIGHT", detail, "RIGHT", -126, 0)
  composer:SetAutoFocus(false)
  composer:SetTextInsets(7, 6, 0, 0)
  composer:SetScript("OnEscapePressed", function(self)
    self:ClearFocus()
  end)
  composer:SetScript("OnEnterPressed", function(self)
    ns:SendComposer()
    self:ClearFocus()
  end)
  detail.composer = composer

  local send = makeButton(detail, 72, 20, L["Send"], function()
    ns:SendComposer()
  end)
  send:SetPoint("BOTTOMRIGHT", -12, 44)
  detail.send = send

  local focus = makeButton(detail, 118, 20, L["Hand off"], function()
    ns:FocusSelected()
  end)
  focus:SetPoint("RIGHT", send, "LEFT", -6, 0)
  detail.focus = focus

  local hint = colorize(fontString(detail, "OVERLAY", FONT_SMALL), DIM)
  hint:SetPoint("BOTTOMLEFT", 12, 22)
  hint:SetText(L["Enter sends the reply and syncs. Hand off copies the session for the desktop app."])
  detail.hint = hint

  detail:Hide()
  self.Detail = detail
  return detail
end

--- Open the detail pane. `takeFocus` is explicit because grabbing the keyboard
--- puts movement keys into the edit box until the player presses Escape.
function ns:OpenDetail(session, takeFocus)
  if not session then
    return
  end

  self.selected = session
  local detail = self:BuildDetail()

  fitText(detail.title, session.title or "", DETAIL_TEXT_WIDTH)
  detail.meta:SetText(self:WhereText(session))

  -- Messages and cost are in the facts block below; this line is what the agent
  -- is doing right now, which is the one thing not stated anywhere else.
  fitText(detail.stats,
    (session.activity and session.activity ~= "") and session.activity or L["not reporting an activity"], DETAIL_TEXT_WIDTH)

  local facts = {
    { L["session"], session.id or "" },
    { L["machine"], (session.host and session.host ~= "local") and session.host or L["this machine"] },
    { L["project"], (session.project ~= "" and session.project) or "-" },
    { L["profile"], (session.profile ~= "" and session.profile) or "-" },
    { L["messages"], tostring(session.messages or 0) },
    { L["cost"], string.format("$%.2f", session.cost or 0) },
  }
  for index, entry in ipairs(detail.facts) do
    local fact = facts[index]
    entry.key:SetText(fact[1] .. ":")
    fitText(entry.value, fact[2], math.floor(DETAIL_TEXT_WIDTH / 2) - 72)
  end

  -- Say where the reply goes before the player types it, not after.
  fitText(detail.target,
    L["the reply goes to "] .. (session.host and session.host ~= "local" and session.host or L["this machine"]),
    DETAIL_TEXT_WIDTH)

  detail.status:SetText(table.concat({
    session.label or self:StatusLabel(session.status),
    ns.Lf("last activity %s", self:AgeLabel(session.age)),
    session.id or "",
  }, SEP))
  detail.status:SetTextColor(unpack(ns.STATUS_COLORS[session.status] or DIM))

  local body = session.preview
  if not body or body == "" then
    body = L["no output in the last turn"]
  end
  detail.preview:SetText(shorten(body, 900))

  detail.composer:SetText("")
  detail:Show()

  if takeFocus ~= false then
    detail.composer:SetFocus()
  end
end

function ns:CloseDetail()
  if self.Detail then
    self.Detail:Hide()
  end
  self.selected = nil
end

function ns:FocusSelected()
  local session = self.selected
  if not session then
    return
  end

  if self:QueueFocus(session) then
    self:Print(ns.Lf("queued a hand-off for %s; it goes out with the next sync.", session.id))
    if HermesAIDB.sendOnSync then
      self:Sync("focus")
    end
  end
end

-- --------------------------------------------------------------- settings --

function ns:SettingsOptions()
  return {
    { key = "opportunistic", label = L["Refresh on loading screens"], kind = "bool" },
    { key = "sound", label = L["Sound when something needs you"], kind = "bool" },
    { key = "minimap", label = L["Minimap button"], kind = "bool" },
    { key = "sendOnSync", label = L["Sync when a reply is sent"], kind = "bool" },
    { key = "theme", label = L["Skin"], kind = "theme" },
  }
end

function ns:BuildSettings()
  if self.Settings then
    return self.Settings
  end

  local panel = self:BuildPanel()
  local settings = backdropFrame("Frame", "HermesAISettings", panel, "BackdropTemplate")
  settings:SetPoint("TOPLEFT", 10, ROW_TOP)
  settings:SetPoint("BOTTOMRIGHT", -10, 28)
  settings:EnableMouse(true)
  applyBackdrop(settings, 0.98)

  local title = colorize(fontString(settings, "OVERLAY", FONT_TITLE), TEXT)
  title:SetPoint("TOPLEFT", 12, -10)
  title:SetText(L["Settings"])

  local note = colorize(fontString(settings, "OVERLAY", FONT_SMALL), DIM)
  note:SetPoint("TOPLEFT", title, "BOTTOMLEFT", 0, -6)
  note:SetPoint("RIGHT", settings, "RIGHT", -12, 0)
  note:SetJustifyH("LEFT")
  note:SetText(L["Everything here is stored per account and applies on the next reload."])
  settings.note = note

  settings.lines = {}
  local previous = note
  for index, option in ipairs(self:SettingsOptions()) do
    local line = makeButton(settings, 320, 20, option.label, function()
      ns:ToggleSetting(option)
    end)
    line:SetPoint("TOPLEFT", previous, "BOTTOMLEFT", 0, index == 1 and -16 or -6)
    line.option = option
    settings.lines[index] = line
    previous = line
  end

  local policy = colorize(fontString(settings, "OVERLAY", FONT_SMALL), DIM)
  policy:SetPoint("TOPLEFT", previous, "BOTTOMLEFT", 0, -16)
  policy:SetPoint("RIGHT", settings, "RIGHT", -12, 16)
  policy:SetJustifyH("LEFT")
  policy:SetText("")
  settings.policy = policy

  local sync = makeButton(settings, 200, 20, L["Sync now (reloads the UI)"], function()
    ns:Sync("settings")
  end)
  sync:SetPoint("BOTTOMLEFT", 12, 34)

  local reset = makeButton(settings, 170, 20, L["Reset positions"], function()
    HermesAIDB.panelPoint = nil
    HermesAIDB.point = ns.DEFAULT_DB.point
    local board = ns.Board
    board:ClearAllPoints()
    board:SetPoint(ns.DEFAULT_PANEL_POINT[1], UIParent, ns.DEFAULT_PANEL_POINT[1],
      ns.DEFAULT_PANEL_POINT[2], ns.DEFAULT_PANEL_POINT[3])
    ns.Badge:ClearAllPoints()
    ns.Badge:SetPoint(ns.DEFAULT_BADGE_POINT[1], UIParent, ns.DEFAULT_BADGE_POINT[1],
      ns.DEFAULT_BADGE_POINT[2], ns.DEFAULT_BADGE_POINT[3])
    ns:Print(L["panel and badge positions reset."])
  end)
  reset:SetPoint("BOTTOMRIGHT", -12, 34)

  local close = makeButton(settings, 22, 18, "X", function()
    ns:ToggleSettings()
  end)
  close:SetPoint("TOPRIGHT", -8, -8)

  settings:Hide()
  self.Settings = settings
  return settings
end

function ns:RefreshSettings()
  local settings = self:BuildSettings()

  for _, line in ipairs(settings.lines) do
    local option = line.option
    local label
    if option.kind == "bool" then
      label = option.label .. ": " .. ((HermesAIDB[option.key] ~= false) and L["on"] or L["off"])
    else
      label = option.label .. ": " .. tostring(HermesAIDB[option.key] or "dark")
    end
    setButtonText(line, label)
    fitText(line.label, label, 300)
  end

  local policy = self:SyncPolicy()
  settings.policy:SetText(ns.Lf(
    "A sync reloads the UI, so it is spent carefully: at most once every %d minutes, only when the snapshot is "
      .. "older than %d minutes, never in combat, never inside an instance.",
    math.floor(policy.minInterval / 60), math.floor(policy.staleAfter / 60)))
end

function ns:ToggleSetting(option)
  if option.kind == "bool" then
    HermesAIDB[option.key] = not (HermesAIDB[option.key] ~= false)
  elseif option.kind == "theme" then
    HermesAIDB.theme = (HermesAIDB.theme == "dark") and "classic" or "dark"
    self:RefreshSkin()
  end

  if option.key == "minimap" then
    self:RefreshMinimapButton()
  end

  self:RefreshSettings()
  self:RefreshBadge()
  if self.Board:IsShown() then
    self:RefreshPanel()
  end
end

--- Re-apply the palette everywhere it is used.
---
--- The theme touches textures that are set once at build time (rows, buttons,
--- fields, the minimap button), so switching skins has to walk them; missing one
--- leaves a panel that is half one skin and half the other.
function ns:RefreshSkin()
  applyBackdrop(self.Board)
  applyBackdrop(self.Badge)
  if self.Detail then
    applyBackdrop(self.Detail, 0.97)
    applyBackdrop(self.Detail.composerField, nil, theme().field, theme().buttonEdge)
  end
  if self.Settings then
    applyBackdrop(self.Settings, 0.98)
  end
  applyBackdrop(self.Board.searchField, nil, theme().field, theme().buttonEdge)

  for _, button in ipairs({ self.Board.close, self.Board.minimise, self.Board.settingsButton }) do
    ns:SkinButton(button)
  end
  for _, button in ipairs(self.Board.tabs) do
    ns:SkinButton(button)
  end
  if self.Settings then
    for _, line in ipairs(self.Settings.lines) do
      ns:SkinButton(line)
    end
  end

  self:SkinMinimapButton()

  -- Textures remember the palette they were painted with; clearing the key makes
  -- the next refresh repaint them.
  for _, row in ipairs(self.Board.rows) do
    row.odd.theme = nil
    row.even.theme = nil
    row.highlightTheme = nil
  end
  self:RefreshPanel()
end

function ns:ToggleSettings()
  local settings = self:BuildSettings()
  if settings:IsShown() then
    settings:Hide()
  else
    self:RefreshSettings()
    self:CloseDetail()
    settings:Show()
  end
end

function ns:ToggleCollapsed()
  local panel = self:BuildPanel()
  self.collapsed = not self.collapsed

  if self.collapsed then
    self.expandedHeight = math.max(PANEL_HEIGHT, panel:GetHeight() or PANEL_HEIGHT)
    panel:SetHeight(56)
    self:CloseDetail()
    if self.Settings then
      self.Settings:Hide()
    end
  else
    panel:SetHeight(self.expandedHeight or PANEL_HEIGHT)
  end

  self:RefreshPanel()
end

-- ------------------------------------------------------------------ scroll --

function ns:ScrollRows(delta)
  -- The detail and settings panes cover the list: the wheel must not scroll
  -- something the player cannot see.
  if self.Detail and self.Detail:IsShown() then
    return
  end
  if self.Settings and self.Settings:IsShown() then
    return
  end
  if self.collapsed then
    return
  end

  local sessions = self:Filtered()
  local maxOffset = math.max(0, #sessions - ROW_COUNT)
  if maxOffset == 0 then
    return
  end

  self.offset = math.min(maxOffset, math.max(0, (self.offset or 0) + (delta < 0 and 1 or -1)))
  self:RefreshPanel()
end

--- Page the list, for the keybind and for a player without a wheel.
function ns:ScrollTo(offset)
  if self.collapsed then
    return
  end

  local sessions = self:Filtered()
  self.offset = math.max(0, math.min(math.max(0, #sessions - ROW_COUNT), offset or 0))
  self:RefreshPanel()
end

-- ---------------------------------------------------------------- composer --

function ns:SendComposer()
  local detail = self.Detail
  if not detail then
    return
  end

  local session = self.selected
  if not session then
    self:Print(L["pick a session first (click a row)."])
    return
  end

  local text = detail.composer:GetText()
  if not self:QueueReply(session.id, text, session.host) then
    self:Print(L["nothing to send."])
    return
  end

  detail.composer:SetText("")
  self:Print(ns.Lf("queued reply for %s.", session.id))

  if HermesAIDB.sendOnSync then
    self:Sync("reply")
  else
    self:Print(L["press Sync (or /hermesai sync) to send it."])
  end
end

-- ------------------------------------------------------------------ frames --

function ns:ShowBoard()
  local panel = self:BuildPanel()
  self.offset = self.offset or 0
  self.expandedHeight = PANEL_HEIGHT
  if self.collapsed then
    panel:SetHeight(56)
  end
  self:RefreshPanel()
  panel:Show()
end

function ns:HideBoard()
  if self.Board then
    self.Board:Hide()
  end
end

function ns:ToggleBoard()
  if not self.Board then
    self:BuildUI()
  end

  if self.Board:IsShown() then
    self:HideBoard()
  else
    self:ShowBoard()
  end
end

function ns:BuildUI()
  self.offset = self.offset or 0
  self:BuildBadge()
  self:BuildPanel()
  self:BuildDetail()
  self:BuildSettings()
  self:BuildMinimapButton()
  self:RefreshBadge()
  self:RefreshMinimapButton()
  self:RefreshPanel()
  self:RefreshSettings()

  if self:NewCount() > 0 then
    self:PlayAttention(self:NewCount())
  end
end

--- Drop everything cached about how text fits.
---
--- Fitting is measured, and the measurement is only valid for the font metrics
--- in force when it was taken. A player who changes their UI scale (or drags the
--- window to another monitor) would otherwise keep titles truncated to a width
--- that no longer exists.
function ns:InvalidateLayout()
  if not self.Board then
    return
  end

  for _, row in ipairs(self.Board.rows) do
    row.titleText, row.metaText, row.ageText, row.dotKey, row.titleOffline = nil, nil, nil, nil, nil
    row.odd.theme, row.even.theme, row.highlightTheme = nil, nil, nil
  end
  for _, button in ipairs(self.Board.tabs) do
    button.text = nil
  end
  self.Board.attentionText = nil

  if self.Settings then
    for _, line in ipairs(self.Settings.lines) do
      line.text = nil
    end
  end

  if self.Board:IsShown() then
    self:RefreshPanel()
  end
  self:RefreshBadge()
end

function ns:RefreshAll()
  self:RefreshBadge()
  self:RefreshMinimapButton()
  if self.Board and self.Board:IsShown() then
    self:RefreshPanel()
  end
end

--- The refresh entry point Core calls after a tab or search change.
function ns:RefreshBoard()
  self:RefreshPanel()
end

-- ----------------------------------------------------------------- binding --

function ns:Toggle()
  self:ToggleBoard()
end

--- Keybinds: page the list, for a player without a wheel and for anyone who
--- would rather not spin one through forty sessions.
function ns:PageUp()
  self:ScrollTo((self.offset or 0) - ROW_COUNT)
end

function ns:PageDown()
  self:ScrollTo((self.offset or 0) + ROW_COUNT)
end

--- Keybind: open the detail pane for the selected row (or the first one).
function ns:ReplyToSelected()
  local sessions = self:Filtered()
  local session = self.selected or sessions[1]
  if session then
    self:ShowBoard()
    self:OpenDetail(session, true)
  end
end
