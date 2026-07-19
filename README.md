# ironbci_16
ironbci 16 EEG channels, BLE 5

<div style="display: flex; justify-content: center; align-items: center;">
  <img src="https://github.com/pieeg-club/ironbci_16/blob/main/images/ironbci_gif.gif" width="50%" alt="IronBCI Demo">
</div>


<div align="center">
  <img src="https://github.com/pieeg-club/ironbci_16/blob/main/images/ironbci_gif.gif?raw=true" width="50%" alt="IronBCI Demo">
</div>


ironbci included to [PiEEG-Server software](https://github.com/pieeg-club/PiEEG-server)  
<img src="https://github.com/pieeg-club/ironbci/blob/master/Supplementary%20files/imahe_2.png" alt="general view" title="general view" width="90%" height="90%">


#### Technical Details   
ADC - Two ADS1299   
MCU - STM32WB  
Wireless -BLE5     
Electrodes - Gel and Dry     
Noise - 1.0 µVₚₚ (Peak-to-Peak Noise)    
Software - PiEEG Server     



## 🛠️ Technical Specifications

### Hardware Architecture
*   **Analog-to-Digital Converter (ADC):** Dual **ADS1299** (supporting up to 16 simultaneous channels of high-resolution biopotential data).
*   **Microcontroller (MCU):** **STM32WB** series (Dual-core ARM Cortex-M4/M0+ for robust application processing and dedicated wireless stacks).
*   **Wireless Connectivity:** **Bluetooth Low Energy (BLE 5)** for low-latency, ultra-low-power data transmission.

### Signal Integrity & Electrodes
*   **Ultra-Low Noise:** **1.0 µVₚₚ** (Peak-to-Peak Noise), ensuring clean signal baselines even in challenging environments.
*   **Electrode Compatibility:** Supports both **Gel** (wet) and **Dry** electrode systems for flexible deployment.

### Software Ecosystem
*   **Backend Server:** **PiEEG Server** (for seamless data streaming, processing, and visualization).

---

## 📦 System Overview

```text
[ Electrodes ] ──> [ Dual ADS1299 (ADC) ] ──> [ STM32WB (MCU) ] ──> [ BLE 5 ] ──> [ PiEEG Server ]
  (Gel / Dry)        (16-Ch Acquisition)       (Data Packaging)     (Wireless)
```



#### Warnings
>[!WARNING]
> ironbci is not medical device. You are fully responsible for your personal decision to purchase this device and, ultimately, for its safe use. ironbci is not a medical device and has not been certified by any government regulatory agency for use with the human body. Use it at your own risk.  

>[!CAUTION]
> The device must operate only from a battery - 5 V. Complete isolation from the mains power is required.! The device MUST not be connected to any kind of mains power, via USB or otherwise.   
> Power supply - only battery 5V, please read the [liability](https://pieeg.com/liability/)
>
>
#### Contacts   
https://pieeg.com/   
pieeg@pieeg.com  
