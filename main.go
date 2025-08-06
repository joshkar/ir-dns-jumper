package main

import (
	"fmt"
	"image/color"
	"os/exec"
	"runtime"
	"strings"

	"fyne.io/fyne/v2"
	"fyne.io/fyne/v2/app"
	"fyne.io/fyne/v2/canvas"
	"fyne.io/fyne/v2/container"
	"fyne.io/fyne/v2/layout"
	"fyne.io/fyne/v2/theme"
	"fyne.io/fyne/v2/widget"
)

var dnsProfiles = map[string][]string{
	"Shekan":   {"178.22.122.100", "185.51.200.2"},
	"Google":   {"8.8.8.8", "8.8.4.4"},
	"Electro":  {"78.157.42.101", "78.157.42.100"},
	"Begzar":   {"185.55.226.26", "185.55.225.25"},
	"Radar":    {"10.202.10.10", "10.202.10.11"},
	"Shellter": {"94.103.125.157", "94.103.125.158"},
	"Beshkan":  {"181.41.194.177", "181.41.194.186"},
	"Shatel":   {"85.15.1.14", "85.15.1.15"},
}

type modernTheme struct{ fyne.Theme }

var accent = color.NRGBA{R: 0x4f, G: 0xc3, B: 0xf7, A: 0xff}

func (m modernTheme) Color(name fyne.ThemeColorName, variant fyne.ThemeVariant) color.Color {
	switch name {
	case theme.ColorNamePrimary:
		return accent
	case theme.ColorNameButton:
		return color.NRGBA{R: 0x33, G: 0x99, B: 0x66, A: 0xff}
	case theme.ColorNameError:
		return color.NRGBA{R: 0xf2, G: 0x6c, B: 0x6c, A: 0xff}
	case theme.ColorNameHover:
		return color.NRGBA{R: 0x66, G: 0x66, B: 0x66, A: 0xff}
	case theme.ColorNameFocus:
		return color.NRGBA{R: 0xaa, G: 0xaa, B: 0xaa, A: 0xff}
	}
	return m.Theme.Color(name, variant)
}

func main() {
	a := app.New()
	a.Settings().SetTheme(modernTheme{theme.DefaultTheme()})

	w := a.NewWindow("DNS Jumper")
	w.Resize(fyne.NewSize(800, 450))

	title := canvas.NewText("DNS Jumper", accent)
	title.Alignment = fyne.TextAlignCenter
	title.TextStyle = fyne.TextStyle{Bold: true}
	title.TextSize = 32

	subtitle := canvas.NewText("Quickly toggle between preset DNS profiles", theme.Color(theme.ColorNameForeground))
	subtitle.Alignment = fyne.TextAlignCenter
	subtitle.TextSize = 16

	dnsSelect := widget.NewSelect(getProfileNames(), nil)
	dnsSelect.PlaceHolder = "Choose DNS Profile"

	statusText := canvas.NewText("", color.White)
	statusText.TextSize = 16

	setBtn := widget.NewButtonWithIcon("Apply", theme.ConfirmIcon(), func() {
		if dnsSelect.Selected == "" {
			setStatus(statusText, "Pick profile first.", color.NRGBA{R: 0xff, G: 0x99, A: 0xff})
			return
		}
		if err := setDNS(dnsProfiles[dnsSelect.Selected]); err != nil {
			setStatus(statusText, "❌ "+err.Error(), theme.Color(theme.ColorNameError))
		} else {
			setStatus(statusText, fmt.Sprintf("✔ DNS applied (%s)", dnsSelect.Selected),
				theme.Color(theme.ColorNameSuccess))
		}
	})
	setBtn.Importance = widget.HighImportance

	clearBtn := widget.NewButtonWithIcon("Clear", theme.DeleteIcon(), func() {
		if err := clearDNS(); err != nil {
			setStatus(statusText, "❌ "+err.Error(), theme.Color(theme.ColorNameError))
		} else {
			setStatus(statusText, "✔ DNS cleared", theme.Color(theme.ColorNameSuccess))
		}
	})
	clearBtn.Importance = widget.DangerImportance

	form := container.NewVBox(
		widget.NewForm(
			widget.NewFormItem("", container.NewBorder(
				nil, nil,
				container.NewHBox(widget.NewIcon(theme.SettingsIcon()), widget.NewLabel("Profile")),
				nil,
				dnsSelect,
			)),
		),
		layout.NewSpacer(),
		container.NewCenter(container.NewHBox(setBtn, layout.NewSpacer(), clearBtn)),
		layout.NewSpacer(),
		statusText,
	)

	card := widget.NewCard("", "", form)

	content := container.NewVBox(
		layout.NewSpacer(),
		title,
		subtitle,
		layout.NewSpacer(),
		card,
		layout.NewSpacer(),
	)

	w.SetContent(container.NewCenter(content))
	w.ShowAndRun()
}

func setStatus(t *canvas.Text, msg string, col color.Color) {
	t.Text = msg
	t.Color = col
	t.Refresh()
}

func setDNS(servers []string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "linux":
		strServers := strings.Join(servers, " ")
		cmd = exec.Command("sh", "-c", fmt.Sprintf(`nmcli con mod "$(nmcli -t -f NAME,DEVICE con show --active | grep "$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+' | head -n 1)" | cut -d: -f1)" ipv4.dns "%s" ipv4.ignore-auto-dns yes && nmcli dev reapply "$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+' | head -n 1)"`, strServers))
	case "windows":
		strServers := formatPSArray(servers)
		cmd = exec.Command("powershell", "-Command", fmt.Sprintf("Set-DnsClientServerAddress -InterfaceAlias (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).InterfaceAlias -ServerAddresses (%s)", strServers))
	default:
		return fmt.Errorf("unsupported OS")
	}
	return cmd.Run()
}

func clearDNS() error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "linux":
		cmd = exec.Command("sh", "-c", `nmcli con mod "$(nmcli -t -f NAME,DEVICE con show --active | grep "$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+' | head -n 1)" | cut -d: -f1)" ipv4.dns "" ipv4.ignore-auto-dns no && nmcli dev reapply "$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+' | head -n 1)"`)
	case "windows":
		cmd = exec.Command("powershell", "-Command", "Set-DnsClientServerAddress -InterfaceAlias (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).InterfaceAlias -ResetServerAddresses")
	default:
		return fmt.Errorf("unsupported OS")
	}
	return cmd.Run()
}

func getProfileNames() []string {
	names := make([]string, 0, len(dnsProfiles))
	for k := range dnsProfiles {
		names = append(names, k)
	}
	return names
}

func formatPSArray(servers []string) string {
	quoted := make([]string, len(servers))
	for i, s := range servers {
		quoted[i] = fmt.Sprintf("'%s'", s)
	}
	return strings.Join(quoted, ",")
}
