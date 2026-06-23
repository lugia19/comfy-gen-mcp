// Command mkicon converts a source PNG into a Windows .ico and/or a macOS
// .icns, so the build script needs no external image tooling (Go is already
// required to build the launcher).
//
// Usage:
//
//	mkicon <input.png> <output.ico|-> <output.icns|->
//
// Pass "-" for an output to skip generating it. The input PNG should be square;
// 256x256 or larger is recommended.
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"image"
	"image/png"
	"os"

	"github.com/jackmordaunt/icns/v2"
)

func main() {
	if len(os.Args) != 4 {
		fmt.Fprintln(os.Stderr, "usage: mkicon <input.png> <output.ico|-> <output.icns|->")
		os.Exit(2)
	}
	inPath, icoPath, icnsPath := os.Args[1], os.Args[2], os.Args[3]

	pngBytes, err := os.ReadFile(inPath)
	if err != nil {
		fatal("reading input png", err)
	}
	img, err := png.Decode(bytes.NewReader(pngBytes))
	if err != nil {
		fatal("decoding input png", err)
	}

	if icoPath != "-" {
		if err := writeICO(icoPath, pngBytes, img.Bounds()); err != nil {
			fatal("writing ico", err)
		}
		fmt.Println("wrote", icoPath)
	}

	if icnsPath != "-" {
		if err := writeICNS(icnsPath, img); err != nil {
			fatal("writing icns", err)
		}
		fmt.Println("wrote", icnsPath)
	}
}

// writeICO emits a minimal single-image .ico that embeds the PNG directly
// (PNG-compressed icon entries are supported on Windows Vista and later).
func writeICO(path string, pngBytes []byte, bounds image.Rectangle) error {
	w, h := bounds.Dx(), bounds.Dy()

	var buf bytes.Buffer
	// ICONDIR
	binary.Write(&buf, binary.LittleEndian, uint16(0)) // reserved
	binary.Write(&buf, binary.LittleEndian, uint16(1)) // type: icon
	binary.Write(&buf, binary.LittleEndian, uint16(1)) // image count

	// ICONDIRENTRY (a dimension of 256 is encoded as the byte 0)
	buf.WriteByte(icoDim(w))
	buf.WriteByte(icoDim(h))
	buf.WriteByte(0)                                                  // palette colors
	buf.WriteByte(0)                                                  // reserved
	binary.Write(&buf, binary.LittleEndian, uint16(1))               // color planes
	binary.Write(&buf, binary.LittleEndian, uint16(32))              // bits per pixel
	binary.Write(&buf, binary.LittleEndian, uint32(len(pngBytes)))   // image data size
	binary.Write(&buf, binary.LittleEndian, uint32(6+16))            // offset to image data
	buf.Write(pngBytes)

	return os.WriteFile(path, buf.Bytes(), 0644)
}

func icoDim(d int) byte {
	if d >= 256 {
		return 0
	}
	return byte(d)
}

func writeICNS(path string, img image.Image) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return icns.Encode(f, img)
}

func fatal(context string, err error) {
	fmt.Fprintf(os.Stderr, "mkicon: %s: %v\n", context, err)
	os.Exit(1)
}
