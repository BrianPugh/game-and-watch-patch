#include "flash.h"


void flash_memory_map(OSPI_HandleTypeDef *spi) {
  OSPI_RegularCmdTypeDef cmd = {
    .Instruction = 0xeb,
    .InstructionMode = HAL_OSPI_INSTRUCTION_1_LINE,
    // .SIOOMode = HAL_OSPI_SIOO_INST_EVERY_CMD,
    .AlternateBytesMode = HAL_OSPI_ALTERNATE_BYTES_4_LINES,
    .AddressMode = HAL_OSPI_ADDRESS_4_LINES,
    .OperationType = HAL_OSPI_OPTYPE_COMMON_CFG,
    .FlashId = 0,
    .InstructionDtrMode = HAL_OSPI_INSTRUCTION_DTR_DISABLE,
    .InstructionSize = HAL_OSPI_INSTRUCTION_8_BITS,
    .AddressDtrMode = HAL_OSPI_ADDRESS_DTR_DISABLE,
    .DataMode = HAL_OSPI_DATA_NONE,
    .DataDtrMode = HAL_OSPI_DATA_DTR_DISABLE,
    .DQSMode = HAL_OSPI_DQS_DISABLE,
    .AddressSize = HAL_OSPI_ADDRESS_24_BITS,
    .SIOOMode = HAL_OSPI_SIOO_INST_EVERY_CMD, // HAL_OSPI_SIOO_INST_ONLY_FIRST_CMD
    // .SIOOMode = HAL_OSPI_SIOO_INST_ONLY_FIRST_CMD,
    .DummyCycles = 4,
    // .AlternateBytesSize = 1, //HAL_OSPI_ALTERNATE_BYTES_8_BITS, // ??? firmware uses '1' ??
    .AlternateBytesSize = HAL_OSPI_ALTERNATE_BYTES_8_BITS, // ??? firmware uses '1' ??
    .NbData = 1, // Data length
    .AlternateBytes = 0b000100, //0xa5, // Hmmmm
  };

  HAL_Delay(1);
  
  if(HAL_OSPI_Command(spi, &cmd, 1000) != HAL_OK) {
      Error_Handler();
  }
  HAL_Delay(50);


  OSPI_MemoryMappedTypeDef sMemMappedCfg;

  OSPI_RegularCmdTypeDef sCommand = {
    .Instruction = 0xeb, // 4READ
    .InstructionMode = HAL_OSPI_INSTRUCTION_1_LINE,
    .SIOOMode = HAL_OSPI_SIOO_INST_EVERY_CMD,
    .AlternateBytesMode = HAL_OSPI_ALTERNATE_BYTES_NONE,
    .AddressMode = HAL_OSPI_ADDRESS_4_LINES,
    .OperationType = HAL_OSPI_OPTYPE_READ_CFG,
    .FlashId = 0,
    .InstructionDtrMode = HAL_OSPI_INSTRUCTION_DTR_DISABLE,
    .InstructionSize = HAL_OSPI_INSTRUCTION_8_BITS,
    .AddressDtrMode = HAL_OSPI_ADDRESS_DTR_DISABLE,
    .DataMode = HAL_OSPI_DATA_4_LINES,
    .DataDtrMode = HAL_OSPI_DATA_DTR_DISABLE,
    .DQSMode = HAL_OSPI_DQS_DISABLE,
    .AddressSize = HAL_OSPI_ADDRESS_24_BITS,
    .SIOOMode = HAL_OSPI_SIOO_INST_EVERY_CMD, // HAL_OSPI_SIOO_INST_ONLY_FIRST_CMD
    // .SIOOMode = HAL_OSPI_SIOO_INST_ONLY_FIRST_CMD,
    .DummyCycles = 4,
    // .AlternateBytesSize = 1, //HAL_OSPI_ALTERNATE_BYTES_8_BITS, // ??? firmware uses '1' ??
    .AlternateBytesSize = HAL_OSPI_ALTERNATE_BYTES_8_BITS, // ??? firmware uses '1' ??
    .NbData = 1, // Data length
    .AlternateBytes = 0x00,
  };

  // sCommand.FlashId = HAL_OSPI_FLASH_ID_1;
  // sCommand.InstructionMode = HAL_OSPI_INSTRUCTION_8_LINES;
  // sCommand.InstructionSize = HAL_OSPI_INSTRUCTION_8_BITS;
  // sCommand.InstructionDtrMode = HAL_OSPI_INSTRUCTION_DTR_DISABLE;
  // sCommand.AddressMode = HAL_OSPI_ADDRESS_8_LINES;
  // sCommand.AddressSize = HAL_OSPI_ADDRESS_32_BITS;
  // sCommand.AddressDtrMode = HAL_OSPI_ADDRESS_DTR_ENABLE;
  // sCommand.AlternateBytesMode = HAL_OSPI_ALTERNATE_BYTES_NONE;
  // sCommand.DataMode = HAL_OSPI_DATA_8_LINES;
  // sCommand.DataDtrMode = HAL_OSPI_DATA_DTR_ENABLE;
  // sCommand.DQSMode = HAL_OSPI_DQS_ENABLE;
  // sCommand.SIOOMode = HAL_OSPI_SIOO_INST_EVERY_CMD;
  // sCommand.Address = 0;
  // sCommand.NbData = 1;
  /* Memory-mapped mode configuration for Linear burst write operations */
  // sCommand.OperationType = HAL_OSPI_OPTYPE_WRITE_CFG;
  // sCommand.Instruction = 0x66; /* 4PP / 4 x page program */ // LINEAR_BURST_WRITE;
  // sCommand.DummyCycles = 0; //DUMMY_CLOCK_CYCLES_SRAM_WRITE;
  // if (HAL_OSPI_Command(&hospi1, &sCommand, HAL_OSPI_TIMEOUT_DEFAULT_VALUE) !=
  //     HAL_OK) {
  //   Error_Handler();
  // }
  // HAL_Delay(100);

  //  sCommand.OperationType = HAL_OSPI_OPTYPE_WRITE_CFG;
  // sCommand.Instruction = 0x99; /* 4PP / 4 x page program */ // LINEAR_BURST_WRITE;
  // sCommand.DummyCycles = 0; //DUMMY_CLOCK_CYCLES_SRAM_WRITE;
  // if (HAL_OSPI_Command(&hospi1, &sCommand, HAL_OSPI_TIMEOUT_DEFAULT_VALUE) !=
  //     HAL_OK) {
  //   Error_Handler();
  // }
  // HAL_Delay(100);



  sCommand.OperationType = HAL_OSPI_OPTYPE_WRITE_CFG;
  sCommand.Instruction = 0x38; /* 4PP / 4 x page program */ // LINEAR_BURST_WRITE;
  sCommand.DummyCycles = 0; //DUMMY_CLOCK_CYCLES_SRAM_WRITE;
  if (HAL_OSPI_Command(spi, &sCommand, HAL_OSPI_TIMEOUT_DEFAULT_VALUE) !=
      HAL_OK) {
    Error_Handler();
  }
  /* Memory-mapped mode configuration for Linear burst read operations */
  sCommand.OperationType = HAL_OSPI_OPTYPE_READ_CFG;
  sCommand.Instruction = 0xEB; /* 4READ */  //LINEAR_BURST_READ;
  sCommand.DummyCycles = 6; //DUMMY_CLOCK_CYCLES_SRAM_READ;

  if (HAL_OSPI_Command(spi, &sCommand, HAL_OSPI_TIMEOUT_DEFAULT_VALUE) !=
      HAL_OK) {
    Error_Handler();
  }
  /*Disable timeout counter for memory mapped mode*/
  sMemMappedCfg.TimeOutActivation = HAL_OSPI_TIMEOUT_COUNTER_DISABLE;
  sMemMappedCfg.TimeOutPeriod = 0;
  /*Enable memory mapped mode*/
  if (HAL_OSPI_MemoryMapped(spi, &sMemMappedCfg) != HAL_OK) {
    Error_Handler();
  }
}